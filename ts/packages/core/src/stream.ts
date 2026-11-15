import type { Frame } from "./_wire/ws.js";
import { ITMError, type ErrorCode } from "./errors.js";
import type { GexFilter } from "./filters.js";
import { GexBookTracker, type GexBookState } from "./gexBook.js";
import { LiveGex } from "./live.js";
import { CLIENT_NAME } from "./version.js";

/**
 * The generated protobuf frame bindings, loaded on first connect.
 *
 * They are the largest module in the package and only a subscriber needs
 * them, so they are imported dynamically: a bundle whose client only makes
 * REST calls never pulls them into its entry chunk. Cached at module scope,
 * so a process pays the import once however many clients it builds.
 */
let frameCodec: Promise<typeof import("./_wire/ws.js")> | undefined;
const loadFrameCodec = (): Promise<typeof import("./_wire/ws.js")> =>
  (frameCodec ??= import("./_wire/ws.js"));

export type Topic = "gex";

/**
 * An `itm_` API key connects with protocol 8, the `itmatrixhq.v1`
 * subprotocol and a declared client name, and opens live GEX books. The
 * stream carries GEX books only.
 */
const CLIENT_SUBPROTOCOL = "itmatrixhq.v1";
const KEYED_PROTO = 8;

/** Refusals that retrying cannot fix. The SDK raises and stops. */
const TERMINAL_REASONS: ReadonlySet<string> = new Set([
  "stream_preview",
  "client_outdated",
  "client_required",
  "proto_required",
  "protobuf_required",
]);
/** Refusal codes at authentication that retrying cannot fix. */
const TERMINAL_AUTH_CODES: ReadonlySet<string> = new Set([
  "not_entitled_tier",
  "invalid_params",
]);

const HEARTBEAT_INTERVAL_MS = 20_000; // between our own pings
const DEAD_AFTER_MS = 40_000; // of silence before a connection is dead
const CONNECTION_LIMIT_BACKOFF_MS = 30_000; // minimum wait after connection_limit
const RESYNC_INTERVAL_MS = 5_500; // the server honours one resync per sub per 5 s
const AUTH_TIMEOUT_MS = 20_000;
const MAX_RETRY_AFTER_MS = 300_000;

/**
 * A stream refusal or failure.
 *
 * `reason` is the server's refusal reason (for example `stream_preview` or
 * `connection_limit`) when it sent one. `terminal` is true when the SDK will
 * not retry. `retryAfterSeconds` carries the server's wait hint.
 */
export class StreamError extends ITMError {
  readonly reason?: string;
  readonly terminal: boolean;

  constructor(options: {
    code: ErrorCode;
    message: string;
    reason?: string;
    terminal?: boolean;
    status?: number;
    details?: Record<string, unknown>;
    requestId?: string;
    retryAfterSeconds?: number;
  }) {
    super({
      status: options.status ?? 0,
      code: options.code,
      message: options.message,
      details: options.details,
      requestId: options.requestId,
      retryAfterSeconds: options.retryAfterSeconds,
    });
    this.name = "StreamError";
    this.reason = options.reason;
    this.terminal = options.terminal ?? false;
  }
}

export interface SubscribeOptions {
  symbol: string;
}

export interface StreamConfig {
  url: string;
  ticket: () => Promise<{ ticket: string; exp: number }>;
  /** Builds the socket with the `itmatrixhq.v1` subprotocol. */
  webSocketFactory?: (url: string, protocols?: string[]) => WebSocket;
  maxQueue?: number;
  minReconnectMs?: number;
  maxReconnectMs?: number;
  /**
   * Whether the credential is an `itm_` API key. Only keys can stream.
   * `ITMClient` sets this from its credential.
   */
  keyAuth?: boolean | (() => boolean | Promise<boolean>);
  /** The declared client, `itmatrix-typescript/<version>` by default. */
  client?: string;
  /** Milliseconds between our own pings. Default 20 000. */
  pingIntervalMs?: number;
  /** Milliseconds of silence before the connection is treated as dead. Default 40 000. */
  deadAfterMs?: number;
  /** Minimum wait after a `connection_limit` refusal. Default 30 000. */
  connectionLimitBackoffMs?: number;
  /** Minimum gap between resyncs of one subscription. Default 5 500. */
  resyncIntervalMs?: number;
  /** How long to wait for `authed`. Default 20 000. */
  authTimeoutMs?: number;
  /** Source of jitter in [0, 1). Default `Math.random`. */
  random?: () => number;
}

interface Control {
  op?: string;
  id?: string;
  code?: string;
  reason?: string;
  message?: string;
  limit?: number;
  effective?: unknown;
  reconnect_after_ms?: number;
  t?: unknown;
}

interface Pending<T> {
  resolve: (result: IteratorResult<T>) => void;
  reject: (error: unknown) => void;
}

class ValueQueue<T> {
  private readonly values: T[] = [];
  private readonly pending: Array<Pending<T>> = [];
  private ended = false;
  private error: unknown;

  constructor(
    private readonly maximum: number,
    private readonly overflow: () => void,
  ) {}

  push(value: T): void {
    if (this.ended) return;
    const waiter = this.pending.shift();
    if (waiter) return waiter.resolve({ value, done: false });
    if (this.values.length >= this.maximum) {
      this.fail(new ITMError({
        status: 0,
        code: "unknown",
        message: `stream consumer fell behind (queue limit ${this.maximum})`,
      }));
      this.overflow();
      return;
    }
    this.values.push(value);
  }

  next(): Promise<IteratorResult<T>> {
    if (this.values.length > 0) {
      return Promise.resolve({ value: this.values.shift() as T, done: false });
    }
    if (this.error !== undefined) return Promise.reject(this.error);
    if (this.ended) return Promise.resolve({ value: undefined, done: true });
    return new Promise((resolve, reject) => this.pending.push({ resolve, reject }));
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    for (const waiter of this.pending.splice(0)) {
      waiter.resolve({ value: undefined, done: true });
    }
  }

  fail(error: unknown): void {
    if (this.ended) return;
    this.error = error;
    this.ended = true;
    for (const waiter of this.pending.splice(0)) waiter.reject(error);
  }
}

abstract class Subscription<T> implements AsyncIterableIterator<T> {
  effective?: unknown;
  readonly id: string;
  readonly topic: Topic;
  readonly options: SubscribeOptions;
  protected readonly queue: ValueQueue<T>;
  protected active = true;

  protected constructor(
    id: string,
    topic: Topic,
    options: SubscribeOptions,
    maximum: number,
  ) {
    this.id = id;
    this.topic = topic;
    this.options = options;
    this.queue = new ValueQueue<T>(maximum, () => this.close());
  }

  next(): Promise<IteratorResult<T>> {
    return this.queue.next();
  }

  return(): Promise<IteratorResult<T>> {
    this.close();
    return Promise.resolve({ value: undefined, done: true });
  }

  [Symbol.asyncIterator](): AsyncIterableIterator<T> {
    return this;
  }

  /** End this subscription. Idempotent. The connection closes with the last one. */
  close(): void {
    if (!this.active) return;
    this.active = false;
    this.queue.end();
    this.detach();
  }

  /** Remove this subscription from its stream. */
  protected abstract detach(): void;

  /** Called with every decoded frame. */
  abstract feed(frame: Frame): void;

  /** Called each time the connection is (re)established, before `sub` is sent. */
  onConnect(): void {}

  /** Called when the connection drops and a reconnect follows. */
  onDisconnect(): void {}

  fail(error: unknown): void {
    this.active = false;
    this.queue.fail(error);
  }

  finish(): void {
    this.active = false;
    this.queue.end();
  }
}

/**
 * One symbol's reconstructed GEX book on the shared connection. With an owner
 * (a {@link LiveGex}) every applied state, failure and connection change goes
 * to the owner. Without one it is an async iterator of states, which the
 * transport tests use.
 * @internal
 */
export class BookFeed extends Subscription<GexBookState> {
  private readonly book: GexBookTracker;

  constructor(
    id: string,
    options: SubscribeOptions,
    maximum: number,
    private readonly requestResync: (subscription: BookFeed) => void,
    private readonly stop: (subscription: BookFeed) => void,
    private readonly owner?: LiveGex,
  ) {
    super(id, "gex", options, maximum);
    this.book = new GexBookTracker(options.symbol);
  }

  /** The latest full book, or `undefined` before a keyframe. */
  get state(): GexBookState | undefined {
    return this.book.state;
  }

  get symbol(): string {
    return this.options.symbol;
  }

  protected detach(): void {
    this.stop(this);
  }

  feed(frame: Frame): void {
    if (!this.active || frame.symbol !== this.options.symbol) return;
    const { state, resync } = this.book.apply(frame);
    if (resync) this.requestResync(this);
    if (state === undefined) return;
    if (this.owner !== undefined) this.owner.publish(state);
    else this.queue.push(state);
  }

  onConnect(): void {
    // A new connection starts with a keyframe. Nothing before it applies.
    this.book.reset();
    this.owner?.connected();
  }

  onDisconnect(): void {
    this.owner?.disconnected();
  }

  fail(error: unknown): void {
    super.fail(error);
    this.owner?.fail(error);
  }

  finish(): void {
    super.finish();
    this.owner?.finish(undefined);
  }
}

type AnySubscription = BookFeed;

/** One WebSocket and what is known about it. */
interface Connection {
  readonly socket: WebSocket;
  readonly keyed: boolean;
  authed: boolean;
  dead: boolean;
  lastRx: number;
  heartbeat?: ReturnType<typeof setTimeout>;
  /** Present while the handshake is in flight. */
  settle?: { resolve: () => void; reject: (error: unknown) => void };
  /** Serializes binary messages behind a Blob that is still being read. */
  tail?: Promise<void>;
}

export class Stream {
  private readonly subscriptions = new Map<string, AnySubscription>();
  /** Open live books, and the resolvers of `run()` calls waiting for none. */
  private readonly live = new Set<LiveGex>();
  private idleWaiters: Array<() => void> = [];
  private readonly factory: (url: string, protocols?: string[]) => WebSocket;
  private readonly maxQueue: number;
  private readonly minDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly pingIntervalMs: number;
  private readonly deadAfterMs: number;
  private readonly limitBackoffMs: number;
  private readonly resyncIntervalMs: number;
  private readonly authTimeoutMs: number;
  private readonly random: () => number;
  private readonly resyncSent = new Map<string, number>();
  private decodeFrame?: (bytes: Uint8Array) => Frame;
  private connection?: Connection;
  /** Identifies the reconnect loop that is running, if any. */
  private loop?: object;
  private wake?: () => void;
  private closed = false;
  private nextId = 1;
  private reconnectAfterMs?: number;

  constructor(private readonly config: StreamConfig) {
    const maximum = config.maxQueue ?? 64;
    const minimumDelay = config.minReconnectMs ?? 250;
    const maximumDelay = config.maxReconnectMs ?? 10_000;
    if (!Number.isInteger(maximum) || maximum < 1) {
      throw new TypeError("maxQueue must be a positive integer");
    }
    if (minimumDelay < 0 || maximumDelay < minimumDelay) {
      throw new TypeError("reconnect delays must satisfy 0 <= min <= max");
    }
    const ping = config.pingIntervalMs ?? HEARTBEAT_INTERVAL_MS;
    const dead = config.deadAfterMs ?? DEAD_AFTER_MS;
    if (!(ping > 0) || !(dead > 0)) {
      throw new TypeError("pingIntervalMs and deadAfterMs must be positive");
    }
    this.factory = config.webSocketFactory ?? defaultWebSocket;
    this.maxQueue = maximum;
    this.minDelayMs = minimumDelay;
    this.maxDelayMs = maximumDelay;
    this.pingIntervalMs = ping;
    this.deadAfterMs = dead;
    this.limitBackoffMs = config.connectionLimitBackoffMs ?? CONNECTION_LIMIT_BACKOFF_MS;
    this.resyncIntervalMs = config.resyncIntervalMs ?? RESYNC_INTERVAL_MS;
    this.authTimeoutMs = config.authTimeoutMs ?? AUTH_TIMEOUT_MS;
    this.random = config.random ?? Math.random;
  }

  /**
   * The live GEX book of one symbol. Requires an `itm_` API key.
   *
   * Returns at once and connects in the background. `latest` holds the newest
   * {@link Book}, `on("update", fn)` calls `fn` with each new one, and
   * `await live.changed()` waits for the next. Filters: `{ nearest: 20 }`,
   * `{ within: 0.05 }` (5% of spot) or `{ between: [590, 610] }`, optionally
   * with `top` and `by`. Change them later with `live.update(...)`. Each symbol
   * is its own object, and every object on a client shares one connection.
   */
  gex(symbol: string, options: GexFilter = {}): LiveGex {
    if (typeof symbol !== "string") {
      throw new TypeError("pass one symbol; open one live book per symbol");
    }
    const name = symbol.trim().toUpperCase();
    if (!name) throw new TypeError("symbol is required");
    return new LiveGex(name, options, (live) => {
      const feed = this.bookFeed(name, live, false);
      this.live.add(live);
      return {
        get state() { return feed.state; },
        start: () => this.startFeed(feed),
        close: () => feed.close(),
        released: () => this.liveDone(live),
      };
    });
  }

  /**
   * Resolves when every live book on this stream has closed, at once when
   * none is open. `client.run()` is the usual way to call it.
   */
  run(): Promise<void> {
    if (this.live.size === 0) return Promise.resolve();
    return new Promise<void>((resolve) => { this.idleWaiters.push(resolve); });
  }

  private liveDone(live: LiveGex): void {
    this.live.delete(live);
    if (this.live.size > 0) return;
    const waiters = this.idleWaiters;
    this.idleWaiters = [];
    for (const resolve of waiters) resolve();
  }

  /**
   * A bare book subscription, an async iterator of states. For tests.
   * @internal
   */
  bookFeed(symbol: string, owner?: LiveGex, start = true): BookFeed {
    if (!symbol) throw new TypeError("symbol is required");
    const feed: BookFeed = new BookFeed(
      this.newId(),
      { symbol: symbol.toUpperCase() },
      this.maxQueue,
      (value) => this.requestResync(value),
      (value) => this.remove(value),
      owner,
    );
    if (start) this.startFeed(feed);
    return feed;
  }

  private startFeed(feed: BookFeed): void {
    if (this.connection !== undefined && !this.connection.keyed) {
      feed.fail(notAKey());
      return;
    }
    this.add(feed);
  }

  close(): void {
    this.closed = true;
    this.wake?.();
    for (const subscription of this.subscriptions.values()) subscription.finish();
    this.subscriptions.clear();
    this.resyncSent.clear();
    const connection = this.connection;
    if (connection) {
      this.teardown(connection, 1000, "stream closed");
    }
  }

  private newId(): string {
    return `sdk-${this.nextId++}`;
  }

  private add(subscription: AnySubscription): void {
    this.closed = false;
    this.subscriptions.set(subscription.id, subscription);
    if (this.connection?.authed) this.sendSubscription(subscription);
    else if (this.connection === undefined) this.startLoop(true);
  }

  private remove(subscription: AnySubscription): void {
    if (!this.subscriptions.delete(subscription.id)) return;
    this.resyncSent.delete(subscription.id);
    const connection = this.connection;
    if (connection?.authed) {
      this.send(connection, { op: "unsub", id: subscription.id });
    }
    if (this.subscriptions.size === 0) {
      this.wake?.();
      if (connection?.authed) this.teardown(connection, 1000, "no subscriptions");
    }
  }

  // -- connecting -----------------------------------------------------------

  private startLoop(immediate: boolean): void {
    if (this.loop !== undefined) return;
    const loop = {};
    this.loop = loop;
    void this.reconnect(immediate, loop);
  }

  private async reconnect(immediate: boolean, loop: object): Promise<void> {
    try {
      await this.reconnecting(immediate);
    } finally {
      if (this.loop === loop) this.loop = undefined;
      // A subscription added while this loop was winding down found it still
      // registered and started nothing. Pick it up now.
      if (
        !this.closed && this.loop === undefined &&
        this.connection === undefined && this.subscriptions.size > 0
      ) this.startLoop(true);
    }
  }

  private async reconnecting(immediate: boolean): Promise<void> {
    // The first attempt of an immediate connect is not a retry.
    let failures = immediate ? -1 : 0;
    let explicit: number | undefined = immediate ? 0 : undefined;
    for (;;) {
      const hint = this.reconnectAfterMs;
      this.reconnectAfterMs = undefined;
      let wait: number;
      if (hint !== undefined) wait = hint;
      else if (explicit !== undefined) wait = explicit;
      else {
        const cap = Math.min(
          this.maxDelayMs, this.minDelayMs * 2 ** Math.max(failures, 0),
        );
        wait = this.random() * cap; // full jitter
      }
      if (wait > 0) await this.sleep(wait);
      if (this.closed || this.subscriptions.size === 0) return;
      try {
        await this.attempt();
        return;
      } catch (error) {
        if (this.closed) return;
        const [terminal, next] = this.plan(error);
        if (terminal) {
          this.failAll(error);
          return;
        }
        explicit = next;
        failures += 1;
      }
    }
  }

  /**
   * Decide what a failed connect means: whether it is final, and how long the
   * server asked us to wait in milliseconds (`undefined` for the exponential
   * backoff).
   */
  private plan(error: unknown): [boolean, number | undefined] {
    if (!(error instanceof ITMError)) return [false, undefined]; // network, timeout
    if (error instanceof StreamError && error.terminal) return [true, undefined];
    const reason = reasonOf(error);
    if (reason !== undefined && TERMINAL_REASONS.has(reason)) return [true, undefined];
    if (reason === "connection_limit") {
      return [false, this.limitBackoffMs * (1 + 0.5 * this.random())];
    }
    const hint = error.retryAfterSeconds;
    if (hint !== undefined && hint >= 0) {
      return [false, Math.min(hint * 1000, MAX_RETRY_AFTER_MS)];
    }
    if (error instanceof StreamError || [0, 408, 429].includes(error.status)) {
      return [false, undefined];
    }
    // Other 4xx answers (bad key, revoked, forbidden) will not change on retry.
    return [error.status >= 400 && error.status < 500, undefined];
  }

  private failAll(error: unknown): void {
    let failure = error;
    if (error instanceof ITMError && !(error instanceof StreamError)) {
      failure = new StreamError({
        code: error.code,
        message: error.message,
        reason: reasonOf(error),
        terminal: true,
        status: error.status,
        details: error.details,
        requestId: error.requestId,
        retryAfterSeconds: error.retryAfterSeconds,
      });
    }
    for (const subscription of this.subscriptions.values()) subscription.fail(failure);
    this.subscriptions.clear();
    this.resyncSent.clear();
  }

  private sleep(milliseconds: number): Promise<void> {
    return new Promise((resolve) => {
      const done = (): void => {
        clearTimeout(timer);
        if (this.wake === done) this.wake = undefined;
        resolve();
      };
      const timer = setTimeout(done, milliseconds);
      this.wake = done;
    });
  }

  private async usesKey(): Promise<boolean> {
    const value = this.config.keyAuth;
    return Boolean(typeof value === "function" ? await value() : value);
  }

  /** Mint a ticket, open a socket and authenticate. Throws what the caller plans on. */
  private async attempt(): Promise<void> {
    const keyed = await this.usesKey();
    if (!keyed) {
      for (const subscription of [...this.subscriptions.values()]) {
        if (subscription instanceof BookFeed) {
          this.subscriptions.delete(subscription.id);
          subscription.fail(notAKey());
        }
      }
    }
    if (this.closed || this.subscriptions.size === 0) return;
    const minted = await this.config.ticket();
    const ticket = minted?.ticket;
    if (typeof ticket !== "string" || ticket === "") {
      throw new TypeError("ticket response did not contain a ticket");
    }
    // The codec is resolved before the socket exists, so no frame can
    // arrive while the import is still in flight.
    this.decodeFrame = (await loadFrameCodec()).Frame.decode;
    if (this.closed || this.subscriptions.size === 0) return;
    await this.open(ticket, keyed);
  }

  private open(ticket: string, keyed: boolean): Promise<void> {
    const socket = this.factory(this.config.url, [CLIENT_SUBPROTOCOL]);
    const connection: Connection = {
      socket, keyed, authed: false, dead: false, lastRx: performance.now(),
    };
    this.connection = connection;
    socket.binaryType = "arraybuffer";
    const auth: Record<string, unknown> = {
      op: "auth", ticket, proto: KEYED_PROTO, encoding: "protobuf",
      client: this.config.client ?? CLIENT_NAME,
    };
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("stream authentication timed out")),
        this.authTimeoutMs,
      );
      connection.settle = {
        resolve: () => { clearTimeout(timer); connection.settle = undefined; resolve(); },
        reject: (error) => { clearTimeout(timer); connection.settle = undefined; reject(error); },
      };
      socket.addEventListener("open", () => {
        try {
          socket.send(JSON.stringify(auth));
        } catch (error) {
          connection.settle?.reject(error);
        }
      });
      socket.addEventListener("message", (event) => this.message(connection, event));
      socket.addEventListener("close", () => this.closedByPeer(connection));
      socket.addEventListener("error", () => {
        connection.settle?.reject(new Error("websocket error"));
      });
    }).catch((error: unknown) => {
      this.teardown(connection, 1000, "authentication failed");
      throw error;
    });
  }

  private authenticated(connection: Connection): void {
    connection.authed = true;
    connection.lastRx = performance.now();
    // This attempt is done. A drop from here on starts a new loop.
    this.loop = undefined;
    const settle = connection.settle;
    if (this.subscriptions.size === 0) {
      settle?.resolve();
      this.teardown(connection, 1000, "no subscriptions");
      return;
    }
    for (const subscription of this.subscriptions.values()) {
      subscription.onConnect();
      this.sendSubscription(subscription);
    }
    this.beat(connection, performance.now() + this.pingIntervalMs);
    settle?.resolve();
  }

  // -- heartbeat ------------------------------------------------------------

  /** Ping every interval; drop a connection that has been silent too long. */
  private beat(connection: Connection, nextPing: number): void {
    const now = performance.now();
    const deadAt = connection.lastRx + this.deadAfterMs;
    connection.heartbeat = setTimeout(() => {
      if (connection.dead) return;
      const at = performance.now();
      if (at >= connection.lastRx + this.deadAfterMs) {
        this.lost(connection, 1001, "no data");
        return;
      }
      let upcoming = nextPing;
      if (at >= nextPing) {
        this.send(connection, { op: "ping" });
        upcoming = at + this.pingIntervalMs;
      }
      this.beat(connection, upcoming);
    }, Math.max(Math.min(nextPing, deadAt) - now, 0));
  }

  // -- receiving ------------------------------------------------------------

  private message(connection: Connection, event: MessageEvent): void {
    if (connection.dead) return;
    connection.lastRx = performance.now();
    if (typeof event.data === "string") {
      this.control(connection, event.data);
      return;
    }
    if (!connection.authed) {
      connection.settle?.reject(new ITMError({
        status: 0,
        code: "unknown",
        message: "binary data arrived before stream authentication",
      }));
      return;
    }
    const bytes = viewBytes(event.data);
    if (bytes !== undefined && connection.tail === undefined) {
      this.frame(connection, bytes);
      return;
    }
    // A Blob has to be read asynchronously. Keep order: later frames wait for it.
    const job: Promise<void> = (connection.tail ?? Promise.resolve())
      .then(async () => {
        this.frame(connection, bytes ?? await blobBytes(event.data));
      })
      .catch((cause: unknown) => this.invalidFrame(connection, cause))
      .finally(() => {
        if (connection.tail === job) connection.tail = undefined;
      });
    connection.tail = job;
  }

  private frame(connection: Connection, bytes: Uint8Array): void {
    if (connection.dead) return;
    try {
      const frame = (this.decodeFrame as (bytes: Uint8Array) => Frame)(bytes);
      for (const subscription of [...this.subscriptions.values()]) {
        subscription.feed(frame);
      }
    } catch (cause) {
      this.invalidFrame(connection, cause);
    }
  }

  private invalidFrame(connection: Connection, cause: unknown): void {
    const error = new ITMError({
      status: 0,
      code: "unknown",
      message: "invalid protobuf websocket frame",
      details: { cause: String(cause) },
    });
    for (const subscription of this.subscriptions.values()) subscription.fail(error);
    this.subscriptions.clear();
    this.resyncSent.clear();
    this.teardown(connection, 1002, "invalid protobuf frame");
  }

  private control(connection: Connection, raw: string): void {
    let frame: Control;
    try {
      const value: unknown = JSON.parse(raw);
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new SyntaxError("not an object");
      }
      frame = value as Control;
    } catch {
      const error = new ITMError({
        status: 0, code: "unknown", message: "invalid websocket control frame",
      });
      if (connection.settle) connection.settle.reject(error);
      else this.lost(connection, 1002, "invalid control frame");
      return;
    }
    if (frame.op === "ping") {
      this.send(connection, "t" in frame ? { op: "pong", t: frame.t } : { op: "pong" });
      return;
    }
    if (!connection.authed) {
      // Before authentication only these three mean anything.
      if (frame.op === "authed") this.authenticated(connection);
      else if (frame.op === "nack") {
        connection.settle?.reject(refusal(frame, "stream authentication rejected", true));
      } else if (frame.op === "bye") connection.settle?.reject(goodbye(frame));
      return;
    }
    switch (frame.op) {
      case "ack": {
        const subscription = frame.id ? this.subscriptions.get(frame.id) : undefined;
        if (subscription) subscription.effective = frame.effective;
        break;
      }
      case "nack": {
        const subscription = frame.id ? this.subscriptions.get(frame.id) : undefined;
        if (subscription) {
          this.subscriptions.delete(subscription.id);
          this.resyncSent.delete(subscription.id);
          subscription.fail(refusal(frame, "stream subscription rejected", false));
        }
        break;
      }
      case "bye": {
        const seconds = goodbye(frame).retryAfterSeconds;
        this.reconnectAfterMs = seconds === undefined ? undefined : seconds * 1000;
        this.lost(connection, 1000, "server requested reconnect");
        break;
      }
    }
  }

  private closedByPeer(connection: Connection): void {
    if (connection.dead) return;
    if (connection.settle) {
      connection.settle.reject(new Error("connection closed before authentication"));
      return;
    }
    this.lost(connection, 1000, "closed");
  }

  /** The connection is over. Drop it and, if anyone is listening, reconnect. */
  private lost(connection: Connection, code: number, reason: string): void {
    this.teardown(connection, code, reason);
    if (!this.closed && this.subscriptions.size > 0) {
      for (const subscription of [...this.subscriptions.values()]) subscription.onDisconnect();
      this.startLoop(false);
    }
  }

  private teardown(connection: Connection, code: number, reason: string): void {
    if (connection.dead) return;
    connection.dead = true;
    clearTimeout(connection.heartbeat);
    connection.settle?.reject(new Error("connection closed"));
    if (this.connection === connection) this.connection = undefined;
    try {
      connection.socket.close(code, reason);
    } catch {
      // Already closing.
    }
  }

  // -- sending --------------------------------------------------------------

  private send(connection: Connection, value: Record<string, unknown>): void {
    if (connection.dead || connection.socket.readyState !== 1) return;
    try {
      connection.socket.send(JSON.stringify(value));
    } catch {
      // A dead socket closes, and the close event reconnects.
    }
  }

  private sendSubscription(subscription: AnySubscription): void {
    const connection = this.connection;
    if (!connection?.authed) return;
    const options = subscription.options;
    this.send(connection, {
      op: "sub",
      id: subscription.id,
      topic: subscription.topic,
      symbol: options.symbol,
    });
  }

  /** Ask for a fresh keyframe, at most once per interval per subscription. */
  private requestResync(subscription: BookFeed): void {
    const now = performance.now();
    const last = this.resyncSent.get(subscription.id);
    if (last !== undefined && now - last < this.resyncIntervalMs) return;
    this.resyncSent.set(subscription.id, now);
    const connection = this.connection;
    if (connection?.authed) this.send(connection, { op: "resync", id: subscription.id });
  }
}

function notAKey(): StreamError {
  return new StreamError({
    code: "invalid_params",
    reason: "api_key_required",
    terminal: true,
    message: "live GEX books need an itm_ API key",
  });
}

function reasonOf(error: ITMError): string | undefined {
  const reason = (error as { reason?: unknown }).reason ?? error.details?.reason;
  return typeof reason === "string" ? reason : undefined;
}

/** A `nack` as an error. At authentication, some refusals are final. */
function refusal(control: Control, fallback: string, auth: boolean): StreamError {
  const code = typeof control.code === "string" ? control.code : "unknown";
  const reason = typeof control.reason === "string" ? control.reason : undefined;
  return new StreamError({
    code: sdkCode(code),
    message: control.message || reason || fallback,
    reason,
    terminal: !auth ||
      (reason !== undefined && TERMINAL_REASONS.has(reason)) ||
      TERMINAL_AUTH_CODES.has(code),
    details: control.limit !== undefined ? { limit: control.limit } : undefined,
  });
}

/** A `bye` as an error carrying how long to wait before reconnecting. */
function goodbye(control: Control): StreamError {
  const code = typeof control.code === "string"
    ? control.code
    : typeof control.reason === "string" ? control.reason : "unknown";
  const hint = control.reconnect_after_ms;
  let wait: number | undefined;
  if (typeof hint === "number" && Number.isFinite(hint) && hint >= 0) wait = hint / 1000;
  else if (code === "reauth") wait = 0; // the ticket expired: mint a new one now
  return new StreamError({
    code: sdkCode(code),
    message: `stream closed by server (${code})`,
    reason: typeof control.reason === "string" ? control.reason : code,
    retryAfterSeconds: wait,
  });
}

/** The bytes of a message that is already in memory. */
function viewBytes(data: unknown): Uint8Array | undefined {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  return undefined;
}

async function blobBytes(data: unknown): Promise<Uint8Array> {
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    return new Uint8Array(await data.arrayBuffer());
  }
  throw new TypeError("unsupported websocket message type");
}

function defaultWebSocket(url: string, protocols?: string[]): WebSocket {
  if (!globalThis.WebSocket) {
    throw new StreamError({
      code: "unknown",
      message: "no WebSocket implementation is available",
      terminal: true,
    });
  }
  return protocols
    ? new globalThis.WebSocket(url, protocols)
    : new globalThis.WebSocket(url);
}

function sdkCode(value?: string): ErrorCode {
  const known = new Set([
    "unauthenticated", "token_expired", "invalid_ticket", "not_entitled_tier",
    "not_entitled_symbol", "attestation_required", "rate_limited",
    "quota_exceeded", "invalid_params", "not_found", "range_capped",
    "partial_data", "internal",
  ]);
  return known.has(value ?? "") ? value as ErrorCode : "unknown";
}
