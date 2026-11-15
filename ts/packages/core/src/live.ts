/**
 * Live GEX books: a long-lived object per symbol that keeps the newest book.
 *
 * `client.stream.gex("SPY", { nearest: 20 })` returns a {@link LiveGex} at once
 * and connects in the background. Read `latest` any time, listen with
 * `on("update" | "error" | "status", fn)`, `await changed()` for the next book,
 * or `for await (const book of live)`. Every path is conflated: a slow reader
 * or handler gets the newest book, never a backlog. All live objects of one
 * client share one connection, and each symbol is its own object with no
 * ordering promise across symbols.
 */
import { buildFilter, type GexBookFilter, type GexFilter } from "./filters.js";
import type { GexBookReasonName, GexBookState } from "./gexBook.js";

/** One strike of a book: dollars of dealer hedging per $1 move. */
export interface GexRow {
  readonly strike: number;
  /** Call GEX, positive. */
  readonly call: number;
  /** Put GEX, negative. */
  readonly put: number;
  /** `call + put`. */
  readonly total: number;
}

/** Bookkeeping you rarely need. */
export interface BookMeta {
  readonly bookId: bigint;
  readonly version: number;
  /** The strikes whose values moved in the frame behind this book (all of them for a full book). */
  readonly changed: readonly number[];
  /** Why the server sent a full book, or `null` for an update. */
  readonly reason: GexBookReasonName | null;
}

/** The book's columns, for vectorised work. Do not modify them. */
export interface BookColumns {
  readonly strike: Float64Array;
  readonly call: Float32Array;
  readonly put: Float32Array;
  readonly total: Float64Array;
}

/**
 * One immutable GEX book for one symbol. Iterate it for {@link GexRow} rows in
 * strike order. `netGex` is the whole-chain total, not the sum of the rows.
 */
export class Book implements Iterable<GexRow> {
  private cachedRows?: readonly GexRow[];

  /** @internal */
  constructor(private readonly state: GexBookState) {}

  get symbol(): string { return this.state.symbol; }
  get spot(): number { return this.state.spot; }
  get netGex(): number { return this.state.netGex; }
  /** The zero-gamma strike, or `null` when the chain has none. */
  get flip(): number | null { return this.state.flipPoint; }
  /** When the book was computed. */
  get time(): Date { return this.state.capturedAt; }
  /** Seconds since the book was computed, read now. */
  get age(): number { return (Date.now() - this.state.capturedAt.getTime()) / 1000; }
  get length(): number { return this.state.strikes.length; }

  get rows(): readonly GexRow[] {
    if (this.cachedRows === undefined) {
      const s = this.state;
      const rows: GexRow[] = new Array(s.strikes.length);
      for (let i = 0; i < rows.length; i += 1) {
        rows[i] = Object.freeze({
          strike: s.strikes[i]!, call: s.callGex[i]!, put: s.putGex[i]!, total: s.gex[i]!,
        });
      }
      this.cachedRows = Object.freeze(rows);
    }
    return this.cachedRows;
  }

  get columns(): BookColumns {
    const s = this.state;
    return { strike: s.strikes, call: s.callGex, put: s.putGex, total: s.gex };
  }

  get meta(): BookMeta {
    const s = this.state;
    return Object.freeze({
      bookId: s.bookId,
      version: s.version,
      changed: Array.from(s.changed, (i) => s.strikes[i]!),
      reason: s.reason,
    });
  }

  /** The row at `strike` dollars, or `undefined` when the book does not hold it. */
  get(strike: number): GexRow | undefined {
    const key = Math.round(strike * 1000);
    const keys = this.state.strikesThousandths;
    let lo = 0;
    let hi = keys.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const value = keys[mid]!;
      if (value === key) return this.rows[mid];
      if (value < key) lo = mid + 1;
      else hi = mid - 1;
    }
    return undefined;
  }

  [Symbol.iterator](): Iterator<GexRow> {
    return this.rows[Symbol.iterator]();
  }

  toString(): string {
    return `Book(${this.symbol} spot=${this.spot} netGex=${Math.round(this.netGex)} ` +
      `flip=${this.flip} strikes=${this.length} at ${this.time.toISOString()})`;
  }
}

export type LiveGexStatus = "connecting" | "live" | "reconnecting" | "closed";

/** What each event delivers. */
export interface LiveGexEvents {
  update: Book;
  error: unknown;
  status: LiveGexStatus;
}

type Handler<T> = (value: T) => void | Promise<void>;

/** The transport side a live book needs from the stream. @internal */
export interface BookFeedPort {
  readonly state: GexBookState | undefined;
  start(): void;
  close(): void;
  /** Called once when the live book has finished, for `client.run()`. */
  released(): void;
}

class Waiters {
  private readonly list: Array<{ resolve: (book: Book) => void; reject: (error: unknown) => void }> = [];

  wait(timeoutMs: number | undefined, symbol: string): Promise<Book> {
    return new Promise<Book>((resolve, reject) => {
      const entry = { resolve, reject };
      this.list.push(entry);
      if (timeoutMs !== undefined) {
        setTimeout(() => {
          const at = this.list.indexOf(entry);
          if (at < 0) return;
          this.list.splice(at, 1);
          reject(new Error(`no new GEX book for ${symbol} within ${timeoutMs} ms`));
        }, timeoutMs);
      }
    });
  }

  resolve(book: Book): void {
    for (const entry of this.list.splice(0)) entry.resolve(book);
  }

  reject(error: unknown): void {
    for (const entry of this.list.splice(0)) entry.reject(error);
  }
}

/**
 * The live GEX book of one symbol, kept current in the background.
 *
 * Created by `client.stream.gex(...)`. `latest` is the newest {@link Book}
 * (`undefined` before the first) and `age` its age in seconds. Handlers get
 * `"update"` (a {@link Book}), `"error"` (the error) and `"status"`
 * (`"connecting"`, `"live"`, `"reconnecting"` or `"closed"`). A handler that
 * throws or rejects is reported to the `"error"` handlers and never stops the
 * stream. A terminal refusal (for example `client_outdated`) is an `"error"`
 * with a {@link StreamError}, then the status becomes `"closed"`.
 *
 * Conflation is the contract: `latest`, handlers, `changed()` and
 * `for await` always get the newest book. An async handler still running when
 * a newer book arrives is next called with the newest one.
 */
export class LiveGex implements AsyncIterable<Book> {
  readonly symbol: string;
  latest?: Book;
  status: LiveGexStatus = "connecting";
  error?: unknown;
  private currentFilter?: GexBookFilter;
  private currentOptions: GexFilter;
  private readonly handlers: { [K in keyof LiveGexEvents]: Array<Handler<LiveGexEvents[K]>> } = {
    update: [], error: [], status: [],
  };
  private readonly busy = new Set<Handler<Book>>();
  private readonly pending = new Map<Handler<Book>, Book>();
  private readonly waiters = new Waiters();
  private readonly watchers = new Set<{ book?: Book; done: boolean; wake?: () => void }>();
  private closedFlag = false;
  private feed!: BookFeedPort;

  /** @internal */
  constructor(symbol: string, options: GexFilter, attach: (live: LiveGex) => BookFeedPort) {
    this.currentFilter = buildFilter(options);
    this.currentOptions = { ...options };
    this.symbol = symbol;
    this.feed = attach(this);
    this.feed.start();
  }

  /** Seconds since `latest` was computed, or `undefined` before the first book. */
  get age(): number | undefined {
    return this.latest?.age;
  }

  /** The filter options in force (empty for the whole book). */
  get filter(): GexFilter {
    return { ...this.currentOptions };
  }

  get closed(): boolean {
    return this.closedFlag;
  }

  /** Call `handler` on every `event`. Returns a function that removes it. */
  on<K extends keyof LiveGexEvents>(event: K, handler: Handler<LiveGexEvents[K]>): () => void {
    const list = this.handlers[event] as Array<Handler<LiveGexEvents[K]>> | undefined;
    if (list === undefined) throw new RangeError(`event must be update, error or status, not "${String(event)}"`);
    if (typeof handler !== "function") throw new TypeError("handler must be a function");
    list.push(handler);
    return () => this.off(event, handler);
  }

  off<K extends keyof LiveGexEvents>(event: K, handler: Handler<LiveGexEvents[K]>): void {
    const list = this.handlers[event] as Array<Handler<LiveGexEvents[K]>>;
    const at = list.indexOf(handler);
    if (at >= 0) list.splice(at, 1);
  }

  /**
   * Replace the filter, for example `update({ nearest: 40 })`. `update({})`
   * shows the whole book. The book already held is re-filtered and emitted at once.
   */
  update(options: GexFilter = {}): void {
    this.currentFilter = buildFilter(options);
    this.currentOptions = { ...options };
    const state = this.feed.state;
    if (state !== undefined) this.publish(state);
  }

  /** The first book: `latest` if there is one, else wait for it. */
  ready(timeoutMs?: number): Promise<Book> {
    return this.latest !== undefined ? Promise.resolve(this.latest) : this.changed(timeoutMs);
  }

  /** Wait for the next book after now. Rejects with the stream's error if it fails. */
  changed(timeoutMs?: number): Promise<Book> {
    if (this.error !== undefined) return Promise.reject(this.error);
    if (this.closedFlag) return Promise.reject(new Error(`the live GEX book for ${this.symbol} is closed`));
    return this.waiters.wait(timeoutMs, this.symbol);
  }

  /** Stop and release the subscription. The connection closes with the last one. Idempotent. */
  close(): void {
    if (this.closedFlag) return;
    this.finish(undefined);
    this.feed.close();
  }

  /** Books as they arrive. Conflated: each step gets the newest unread book. */
  async *[Symbol.asyncIterator](): AsyncIterator<Book> {
    const watcher: { book?: Book; done: boolean; wake?: () => void } = {
      book: this.latest, done: this.closedFlag,
    };
    this.watchers.add(watcher);
    try {
      while (true) {
        if (watcher.book !== undefined) {
          const book = watcher.book;
          watcher.book = undefined;
          yield book;
          continue;
        }
        if (watcher.done) {
          if (this.error !== undefined) throw this.error;
          return;
        }
        await new Promise<void>((resolve) => { watcher.wake = resolve; });
        watcher.wake = undefined;
      }
    } finally {
      this.watchers.delete(watcher);
    }
  }

  toString(): string {
    return `LiveGex(${this.symbol} ${this.status} ${JSON.stringify(this.currentOptions)})`;
  }

  // -- feed side --------------------------------------------------------

  /** @internal */
  publish(state: GexBookState): void {
    if (this.closedFlag) return;
    let view = state;
    if (this.currentFilter !== undefined) {
      try {
        view = this.currentFilter(state);
      } catch (error) {
        this.emit("error", error);
        return;
      }
    }
    const book = new Book(view);
    this.latest = book;
    this.waiters.resolve(book);
    for (const watcher of this.watchers) {
      watcher.book = book;
      watcher.wake?.();
    }
    for (const handler of [...this.handlers.update]) this.deliver(handler, book);
  }

  /** @internal */
  connected(): void { this.setStatus("live"); }

  /** @internal */
  disconnected(): void { if (!this.closedFlag) this.setStatus("reconnecting"); }

  /** @internal */
  fail(error: unknown): void {
    if (this.closedFlag) return;
    this.error = error;
    this.emit("error", error);
    this.finish(error);
  }

  /** @internal */
  finish(error: unknown): void {
    if (this.closedFlag) return;
    this.closedFlag = true;
    this.waiters.reject(error ?? new Error(`the live GEX book for ${this.symbol} is closed`));
    for (const watcher of this.watchers) {
      watcher.done = true;
      watcher.wake?.();
    }
    this.setStatus("closed");
    this.pending.clear();
    this.feed?.released();
  }

  private setStatus(status: LiveGexStatus): void {
    if (status === this.status) return;
    this.status = status;
    this.emit("status", status);
  }

  private emit<K extends "error" | "status">(event: K, value: LiveGexEvents[K]): void {
    for (const handler of [...this.handlers[event]] as Array<Handler<LiveGexEvents[K]>>) {
      try {
        const result = handler(value);
        if (result instanceof Promise) result.catch((error: unknown) => this.handlerFailed(event, error));
      } catch (error) {
        this.handlerFailed(event, error);
      }
    }
  }

  /** Run an update handler; an async one runs once at a time and next gets the newest book. */
  private deliver(handler: Handler<Book>, book: Book): void {
    if (this.busy.has(handler)) {
      this.pending.set(handler, book);
      return;
    }
    let result: void | Promise<void>;
    try {
      result = handler(book);
    } catch (error) {
      this.handlerFailed("update", error);
      return;
    }
    if (!(result instanceof Promise)) return;
    this.busy.add(handler);
    const settle = (): void => {
      this.busy.delete(handler);
      const next = this.pending.get(handler);
      if (next === undefined) return;
      this.pending.delete(handler);
      if (!this.closedFlag) this.deliver(handler, next);
    };
    result.then(settle, (error: unknown) => {
      this.handlerFailed("update", error);
      settle();
    });
  }

  private handlerFailed(event: keyof LiveGexEvents, error: unknown): void {
    if (event === "error") {
      console.warn("itmatrix: an error handler failed", error);
      return;
    }
    this.emit("error", error);
  }
}

// `await using live = client.stream.gex(...)` where the runtime has explicit
// resource management. Added at runtime so older targets still type-check.
const disposeSymbols = Symbol as unknown as { asyncDispose?: symbol; dispose?: symbol };
for (const key of [disposeSymbols.asyncDispose, disposeSymbols.dispose]) {
  if (typeof key === "symbol") {
    Object.defineProperty(LiveGex.prototype, key, {
      value(this: LiveGex) { this.close(); },
      configurable: true,
      writable: true,
    });
  }
}
