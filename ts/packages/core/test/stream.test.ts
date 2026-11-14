import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import {
  all,
  GexBookSubscription,
  ITMClient,
  ITMError,
  Stream,
  StreamError,
  streamWire,
  type GexBookState,
  nearest,
  type StreamConfig,
  top,
} from "../src/index.js";

type Listener = (event: Event | MessageEvent) => void;
type Control = Record<string, unknown>;

class FakeSocket {
  binaryType = "";
  readyState = 0;
  readonly sent: Control[] = [];
  private readonly listeners = new Map<string, Listener[]>();

  /** `replies` is what the server answers to the auth frame. Default: authed. */
  constructor(
    readonly protocols?: string[],
    private readonly replies: Control[] = [{ op: "authed" }],
  ) {
    queueMicrotask(() => {
      this.readyState = 1;
      this.emit("open", {} as Event);
    });
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  send(raw: string): void {
    const control = JSON.parse(raw) as Control;
    this.sent.push(control);
    if (control.op === "auth") {
      queueMicrotask(() => {
        for (const reply of this.replies) this.control(reply);
      });
    }
  }

  close(_code?: number, _reason?: string): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit("close", {} as Event);
  }

  get closed(): boolean {
    return this.readyState === 3;
  }

  control(value: Control): void {
    this.emit("message", { data: JSON.stringify(value) } as MessageEvent);
  }

  frame(value: streamWire.Frame): void {
    this.bytes(streamWire.Frame.encode(value).finish());
  }

  bytes(data: Uint8Array | ArrayBuffer): void {
    this.emit("message", { data } as MessageEvent);
  }

  private emit(type: string, event: Event | MessageEvent): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

async function waitFor(predicate: () => boolean, timeoutMs = 3000): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (!predicate()) {
    if (performance.now() > deadline) throw new Error("condition was not reached");
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

const subs = (socket: FakeSocket): Control[] => socket.sent.filter((m) => m.op === "sub");

describe("shared protobuf stream", () => {
  it("uses one socket and routes frames to direct subscriptions", async () => {
    let tickets = 0;
    const sockets: FakeSocket[] = [];
    const stream = new Stream({
      url: "wss://stream.test/v2/ws",
      ticket: async () => ({ ticket: `ticket-${++tickets}`, exp: 1 }),
      webSocketFactory: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
    });

    const stocks = stream.stocks("spy");
    const spot = stream.spot("spy");
    await waitFor(() => sockets[0]?.sent.filter((value) => value.op === "sub").length === 2);

    const socket = sockets[0]!;
    const stockResult = stocks.next();
    socket.frame({
      symbol: "SPY",
      trade: { tsMs: 7n, priceScaled: 6_000_000, size: 2 },
    });
    const spotResult = spot.next();
    socket.frame({
      symbol: "SPY",
      spot: { tsMs: 8n, priceScaled: 6_000_100 },
    });
    socket.control({ op: "ack", id: stocks.id, effective: { symbol: "SPY" } });
    socket.control({ op: "ping", t: 42 });

    expect((await stockResult).value?.trade?.tsMs).toBe(7n);
    expect((await spotResult).value?.spot?.tsMs).toBe(8n);
    expect(stocks.effective).toEqual({ symbol: "SPY" });
    expect(tickets).toBe(1);
    expect(sockets).toHaveLength(1);
    // A token credential keeps protocol 3 and offers no subprotocol.
    expect(socket.sent[0]).toEqual({
      op: "auth", ticket: "ticket-1", proto: 3, encoding: "protobuf",
    });
    expect(socket.protocols).toBeUndefined();
    expect(socket.sent).toContainEqual({ op: "pong", t: 42 });

    await stocks.return();
    await spot.return();
    stream.close();
  });

  it("mints a fresh ticket and resubscribes only after disconnect", async () => {
    let tickets = 0;
    const sockets: FakeSocket[] = [];
    const stream = new Stream({
      url: "wss://stream.test/v2/ws",
      ticket: async () => ({ ticket: `ticket-${++tickets}`, exp: 1 }),
      minReconnectMs: 0,
      maxReconnectMs: 0,
      webSocketFactory: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
    });

    const stocks = stream.stocks("SPY");
    await waitFor(() => sockets[0]?.sent.some((value) => value.op === "sub") === true);
    sockets[0]!.close(1006);
    await waitFor(() => sockets[1]?.sent.some((value) => value.op === "sub") === true);

    const result = stocks.next();
    sockets[1]!.frame({
      symbol: "SPY",
      trade: { tsMs: 9n, priceScaled: 6_000_200, size: 1 },
    });

    expect((await result).value?.trade?.tsMs).toBe(9n);
    expect(tickets).toBe(2);
    expect(sockets).toHaveLength(2);
    await stocks.return();
    stream.close();
  });
});

// ---------------------------------------------------------------------------
// Keyed GEX books (v2, proto 8). The frames are the backend's golden vectors,
// copied verbatim to test/fixtures (see gexBook.test.ts).
// ---------------------------------------------------------------------------

const VECTORS = (
  JSON.parse(
    readFileSync(new URL("./fixtures/gex_strike_book_vectors.json", import.meta.url), "utf8"),
  ) as { steps: Array<{ name: string; frame_hex: string }> }
).steps;
const hexOf = (name: string): string => VECTORS.find((step) => step.name === name)!.frame_hex;
const KEYFRAME_HEX = hexOf("keyframe"); // version 7
const UPDATE_HEX = hexOf("update_1"); // version 8, index [1, 4]
const UPDATE2_HEX = hexOf("update_2"); // version 9, index [0, 2], no flip point
const UPDATE_CRC = 652_656_468;

const PACKAGE_VERSION = (
  JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")) as {
    version: string;
  }
).version;
const CLIENT = `itmatrix-typescript/${PACKAGE_VERSION}`;

const raw = (hex: string): Uint8Array =>
  Uint8Array.from(hex.match(/../g) ?? [], (pair) => parseInt(pair, 16));
const wire = (hex: string): streamWire.Frame => streamWire.Frame.decode(raw(hex));
const encode = (frame: streamWire.Frame): Uint8Array => streamWire.Frame.encode(frame).finish();

function badCrcUpdate(): Uint8Array {
  const frame = wire(UPDATE_HEX);
  frame.gexStrikeBookUpdate!.crc = (UPDATE_CRC ^ 1) >>> 0;
  return encode(frame);
}

function keyframeVersion(version: number): Uint8Array {
  const frame = wire(KEYFRAME_HEX);
  frame.gexStrikeBook!.version = version;
  return encode(frame);
}

/** Reads a subscription without losing a value to an abandoned `next()`. */
class Reader<T> {
  private pending?: Promise<IteratorResult<T>>;

  constructor(private readonly source: AsyncIterator<T>) {}

  async next(timeoutMs = 1000): Promise<T> {
    const result = await this.race(timeoutMs);
    if (result === "timeout") throw new Error("nothing arrived");
    if (result.done) throw new Error("stream ended");
    return result.value;
  }

  /** Resolves true when nothing arrives within the time. */
  async quiet(timeoutMs = 60): Promise<boolean> {
    return (await this.race(timeoutMs)) === "timeout";
  }

  private async race(timeoutMs: number): Promise<IteratorResult<T> | "timeout"> {
    this.pending ??= this.source.next();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), timeoutMs);
    });
    try {
      const result = await Promise.race([this.pending, timeout]);
      if (result !== "timeout") this.pending = undefined;
      return result;
    } catch (error) {
      this.pending = undefined;
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}

/** A scripted server behind Stream: one plan per connect, one entry per mint. */
class Harness {
  readonly sockets: FakeSocket[] = [];
  readonly connectedAt: number[] = [];
  readonly stream: Stream;
  tickets = 0;
  private readonly plans: Array<Control[] | Error | undefined>;
  private readonly mints: Array<Error | undefined>;

  constructor(
    options: Partial<StreamConfig> & {
      plans?: Array<Control[] | Error | undefined>;
      mints?: Array<Error | undefined>;
    } = {},
  ) {
    const { plans, mints, ...config } = options;
    this.plans = [...(plans ?? [])];
    this.mints = [...(mints ?? [])];
    this.stream = new Stream({
      url: "wss://stream.test/v2/ws",
      ticket: async () => {
        this.tickets += 1;
        const failure = this.mints.shift();
        if (failure) throw failure;
        return { ticket: `ticket-${this.tickets}`, exp: 1 };
      },
      webSocketFactory: (_url, protocols) => {
        const plan = this.plans.shift();
        if (plan instanceof Error) throw plan;
        const socket = new FakeSocket(protocols, plan);
        this.sockets.push(socket);
        this.connectedAt.push(performance.now());
        return socket as unknown as WebSocket;
      },
      keyAuth: true,
      minReconnectMs: 0,
      maxReconnectMs: 0,
      ...config,
    });
  }

  /** Wait until the count-th socket is authenticated and subscribed. */
  async ready(count = 1): Promise<FakeSocket> {
    await waitFor(
      () => this.sockets.length >= count && subs(this.sockets[count - 1]!).length > 0,
    );
    return this.sockets[count - 1]!;
  }
}

describe("keyed GEX book stream", () => {
  const harnesses: Harness[] = [];
  afterEach(() => {
    for (const harness of harnesses.splice(0)) harness.stream.close();
  });
  const harness = (options?: ConstructorParameters<typeof Harness>[0]): Harness => {
    const value = new Harness(options);
    harnesses.push(value);
    return value;
  };

  it("authenticates with protocol 8, the subprotocol and the client name", async () => {
    const h = harness();
    const book = h.stream.gexBook("spy");
    expect(book).toBeInstanceOf(GexBookSubscription);
    expect(book.symbol).toBe("SPY");
    const reader = new Reader(book);
    const socket = await h.ready();

    expect(socket.protocols).toEqual(["itmatrixhq.v1"]);
    expect(socket.sent[0]).toEqual({
      op: "auth",
      ticket: "ticket-1",
      proto: 8,
      encoding: "protobuf",
      client: CLIENT,
    });
    expect(CLIENT).toMatch(/^itmatrix-typescript\/\d+\.\d+\.\d+/);
    expect(subs(socket)).toEqual([{ op: "sub", id: "sdk-1", topic: "gex", symbol: "SPY" }]);

    socket.bytes(raw(KEYFRAME_HEX));
    const first = await reader.next();
    expect([first.symbol, first.version, first.reason]).toEqual(["SPY", 7, "subscribe"]);
    expect(Array.from(first.strikes)).toEqual([595, 600, 602.5, 605, 610]);
    socket.bytes(raw(UPDATE_HEX));
    const second = await reader.next();
    expect([second.version, Array.from(second.changed), second.spot]).toEqual([
      8, [1, 4], 601.5,
    ]);
    socket.bytes(raw(UPDATE2_HEX));
    const third = await reader.next();
    expect([third.version, Array.from(third.changed), third.flipPoint]).toEqual([9, [0, 2], null]);
    expect(socket.sent).not.toContainEqual({ op: "resync", id: "sdk-1" });
  });

  it("accepts an ArrayBuffer message as a real socket delivers it", async () => {
    const h = harness();
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    const bytes = raw(KEYFRAME_HEX);
    socket.bytes(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
    expect((await reader.next()).version).toBe(7);
  });

  it("lets a credential that is not an itm_ key use neither a book nor protocol 8", async () => {
    const h = harness({ keyAuth: false });
    const book = h.stream.gexBook("SPY");
    const error = await book.next().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(StreamError);
    expect((error as StreamError).reason).toBe("api_key_required");
    expect((error as StreamError).terminal).toBe(true);
    expect(h.tickets).toBe(0);
    expect(h.sockets).toHaveLength(0);
  });

  it("resyncs once on a CRC failure and drops updates until a keyframe", async () => {
    const h = harness({ resyncIntervalMs: 60_000 });
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    socket.bytes(raw(KEYFRAME_HEX));
    await reader.next();

    socket.bytes(badCrcUpdate());
    await waitFor(() => socket.sent.some((m) => m.op === "resync"));
    // A good update now would be in order, but it is dropped without a second resync.
    socket.bytes(raw(UPDATE_HEX));
    expect(await reader.quiet()).toBe(true);
    expect(socket.sent.filter((m) => m.op === "resync")).toEqual([
      { op: "resync", id: "sdk-1" },
    ]);

    socket.bytes(keyframeVersion(8));
    expect((await reader.next()).version).toBe(8);
    // The book applies updates again after the keyframe. Same strikes, same
    // result as the vector update, one version later: the same CRC.
    const follow = wire(UPDATE_HEX);
    follow.gexStrikeBookUpdate!.version = 9;
    socket.bytes(encode(follow));
    expect((await reader.next()).version).toBe(9);
  });

  it("resyncs on a corrupted keyframe", async () => {
    const h = harness({ resyncIntervalMs: 60_000 });
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    const corrupted = wire(KEYFRAME_HEX);
    corrupted.gexStrikeBook!.callGex[1] = 250_000_016;
    socket.bytes(encode(corrupted));
    await waitFor(() => socket.sent.some((m) => m.op === "resync"));
    expect(await reader.quiet()).toBe(true);
    socket.bytes(raw(KEYFRAME_HEX));
    expect((await reader.next()).version).toBe(7);
  });

  it("repeats the resync after the interval while updates are dropped", async () => {
    const h = harness({ resyncIntervalMs: 50 });
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    socket.bytes(raw(KEYFRAME_HEX));
    await reader.next();
    socket.bytes(raw(UPDATE2_HEX)); // version 9 while at 7: a gap
    await waitFor(() => socket.sent.some((m) => m.op === "resync"));
    await pause(80);
    socket.bytes(raw(UPDATE_HEX));
    await waitFor(() => socket.sent.filter((m) => m.op === "resync").length === 2);
  });

  it("ignores duplicates without a resync", async () => {
    const h = harness();
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    for (const hex of [KEYFRAME_HEX, UPDATE_HEX, UPDATE_HEX, UPDATE_HEX]) {
      socket.bytes(raw(hex));
    }
    await reader.next();
    expect((await reader.next()).version).toBe(8);
    expect(await reader.quiet()).toBe(true);
    expect(socket.sent.some((m) => m.op === "resync")).toBe(false);
  });

  it("mints a fresh ticket, resubscribes and waits for a keyframe after a reconnect", async () => {
    const h = harness();
    const reader = new Reader(h.stream.gexBook("SPY"));
    const first = await h.ready();
    first.bytes(raw(KEYFRAME_HEX));
    await reader.next();

    first.close();
    const second = await h.ready(2);
    expect(h.tickets).toBe(2);
    expect(second.sent[0]).toMatchObject({ ticket: "ticket-2", proto: 8 });
    expect(subs(second)).toEqual(subs(first));
    // An update cannot apply to the old book on the new connection.
    second.bytes(raw(UPDATE_HEX));
    expect(await reader.quiet()).toBe(true);
    second.bytes(keyframeVersion(12));
    expect((await reader.next()).version).toBe(12);
  });

  it.each([
    ["not_entitled_tier", "stream_preview"],
    ["invalid_params", "client_outdated"],
    ["invalid_params", "protobuf_required"],
    ["invalid_params", "proto_required"],
    ["invalid_params", "client_required"],
  ])("raises %s / %s as a terminal error and does not retry", async (code, reason) => {
    const h = harness({ plans: [[{ op: "nack", id: null, code, reason }]] });
    const book = h.stream.gexBook("SPY");
    const error = await book.next().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(StreamError);
    expect(error).toBeInstanceOf(ITMError);
    expect([(error as StreamError).code, (error as StreamError).reason]).toEqual([code, reason]);
    expect((error as StreamError).terminal).toBe(true);
    await pause(50);
    expect([h.tickets, h.sockets.length]).toEqual([1, 1]);
    expect(h.sockets[0]!.closed).toBe(true);
  });

  it("treats a refusal at the ticket mint as terminal", async () => {
    const h = harness({
      mints: [
        new ITMError({
          status: 403,
          code: "not_entitled_tier",
          message: "not on the preview",
          details: { reason: "stream_preview" },
        }),
      ],
    });
    const error = await h.stream.gexBook("SPY").next().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(StreamError);
    expect(error).toMatchObject({ reason: "stream_preview", terminal: true, status: 403 });
    await pause(50);
    expect([h.tickets, h.sockets.length]).toEqual([1, 0]);
  });

  it("does not retry other client errors at the mint", async () => {
    const h = harness({
      mints: [new ITMError({ status: 401, code: "unauthenticated", message: "bad key" })],
    });
    const error = await h.stream.gexBook("SPY").next().catch((cause: unknown) => cause);
    expect(error).toMatchObject({ code: "unauthenticated", terminal: true, status: 401 });
    await pause(50);
    expect(h.tickets).toBe(1);
  });

  it("retries server errors at the mint", async () => {
    const h = harness({
      mints: [new ITMError({ status: 503, code: "internal", message: "down" })],
    });
    h.stream.gexBook("SPY");
    await h.ready();
    expect(h.tickets).toBe(2);
  });

  it("waits for Retry-After when the mint answers 429", async () => {
    const h = harness({
      mints: [
        new ITMError({
          status: 429, code: "rate_limited", message: "slow down", retryAfterSeconds: 0.12,
        }),
      ],
    });
    const started = performance.now();
    h.stream.gexBook("SPY");
    await h.ready();
    expect(h.tickets).toBe(2);
    expect(h.connectedAt[0]! - started).toBeGreaterThanOrEqual(115);
  });

  it("backs off at least the limit after connection_limit, plus jitter", async () => {
    const refusal: Control = {
      op: "nack", id: null, code: "quota_exceeded", reason: "connection_limit", limit: 5,
    };
    const low = harness({
      plans: [[refusal]], connectionLimitBackoffMs: 100, random: () => 0,
    });
    low.stream.gexBook("SPY");
    await low.ready(2);
    expect(low.connectedAt[1]! - low.connectedAt[0]!).toBeGreaterThanOrEqual(95);
    expect(low.tickets).toBe(2);
    // Jitter only adds to the minimum.
    const high = harness({
      plans: [[refusal]], connectionLimitBackoffMs: 100, random: () => 1,
    });
    high.stream.gexBook("SPY");
    await high.ready(2);
    expect(high.connectedAt[1]! - high.connectedAt[0]!).toBeGreaterThanOrEqual(145);
  });

  it.each(["draining", "slow_consumer"])("waits for the hint after %s", async (code) => {
    // Without the hint the default wait would be 5 s.
    const h = harness({ minReconnectMs: 5000, maxReconnectMs: 10_000, random: () => 1 });
    h.stream.gexBook("SPY");
    const first = await h.ready();
    const sentAt = performance.now();
    first.control({ op: "bye", code, reconnect_after_ms: 120 });
    await h.ready(2);
    const waited = h.connectedAt[1]! - sentAt;
    expect(waited).toBeGreaterThanOrEqual(115);
    expect(waited).toBeLessThan(2000);
  });

  it("reconnects at once with a new ticket after reauth", async () => {
    const h = harness({ minReconnectMs: 5000, maxReconnectMs: 10_000, random: () => 1 });
    h.stream.gexBook("SPY");
    const first = await h.ready();
    const sentAt = performance.now();
    first.control({ op: "bye", code: "reauth", reason: "reauth" });
    const second = await h.ready(2);
    expect(h.connectedAt[1]! - sentAt).toBeLessThan(1000);
    expect(second.sent[0]).toMatchObject({ ticket: "ticket-2" });
  });

  it("waits for the hint when draining arrives during authentication", async () => {
    const h = harness({
      plans: [[{ op: "bye", code: "draining", reconnect_after_ms: 100 }]],
      minReconnectMs: 5000,
      maxReconnectMs: 10_000,
      random: () => 1,
    });
    h.stream.gexBook("SPY");
    await h.ready(2);
    expect(h.connectedAt[1]! - h.connectedAt[0]!).toBeGreaterThanOrEqual(95);
  });

  it("backs off exponentially with full jitter after network errors", async () => {
    const draws: number[] = [];
    const draw = (value: number) => () => {
      draws.push(value);
      return value;
    };
    const h = harness({
      plans: [new Error("down"), new Error("down")],
      minReconnectMs: 20,
      maxReconnectMs: 50,
      random: draw(1),
    });
    const started = performance.now();
    h.stream.gexBook("SPY");
    await h.ready();
    // Two failures: caps 20 ms then 40 ms, both at the top of the jitter range.
    expect(h.connectedAt[0]! - started).toBeGreaterThanOrEqual(55);
    expect(draws).toHaveLength(2);
    expect(h.tickets).toBe(3); // one mint per attempt
  });

  it("caps the wait at the maximum and starts anywhere below it", async () => {
    const lucky = harness({
      plans: [new Error("down"), new Error("down")],
      minReconnectMs: 5000,
      maxReconnectMs: 10_000,
      random: () => 0,
    });
    const started = performance.now();
    lucky.stream.gexBook("SPY");
    await lucky.ready();
    expect(lucky.connectedAt[0]! - started).toBeLessThan(1000);
  });

  it("answers server pings and sends its own", async () => {
    const h = harness({ pingIntervalMs: 50, deadAfterMs: 5000 });
    h.stream.gexBook("SPY");
    const socket = await h.ready();
    socket.control({ op: "ping" });
    await waitFor(() => socket.sent.some((m) => m.op === "pong"));
    expect(socket.sent).toContainEqual({ op: "pong" });
    await waitFor(() => socket.sent.filter((m) => m.op === "ping").length >= 2);
  });

  it("treats silence as a dead connection", async () => {
    const h = harness({ pingIntervalMs: 50, deadAfterMs: 200 });
    h.stream.gexBook("SPY");
    const first = await h.ready();
    await h.ready(2);
    expect(first.closed).toBe(true);
    expect(h.tickets).toBe(2);
  });

  it("is kept alive by traffic", async () => {
    const h = harness({ pingIntervalMs: 50, deadAfterMs: 200 });
    h.stream.gexBook("SPY");
    const socket = await h.ready();
    for (let i = 0; i < 10; i += 1) {
      socket.control({ op: "pong" });
      await pause(50);
    }
    expect(h.sockets).toHaveLength(1);
    expect(socket.closed).toBe(false);
  });

  it("carries every book on one connection", async () => {
    const h = harness();
    const spy = h.stream.gexBook("SPY");
    const qqq = h.stream.gexBook("QQQ");
    await waitFor(() => h.sockets.length === 1 && subs(h.sockets[0]!).length === 2);
    const socket = h.sockets[0]!;
    expect(new Set(subs(socket).map((m) => m.symbol))).toEqual(new Set(["SPY", "QQQ"]));
    expect(h.tickets).toBe(1);

    spy.close();
    await waitFor(() => socket.sent.some((m) => m.op === "unsub" && m.id === "sdk-1"));
    expect(socket.closed).toBe(false);
    await qqq.return();
    expect(socket.closed).toBe(true);
    expect(h.sockets).toHaveLength(1);
  });

  it("ends the iterator on close and when a for-await loop breaks", async () => {
    const h = harness();
    const book = h.stream.gexBook("SPY");
    const socket = await h.ready();
    socket.bytes(raw(KEYFRAME_HEX));
    for await (const state of book) {
      expect(state.version).toBe(7);
      break;
    }
    await waitFor(() => socket.closed);
    expect(await book.next()).toEqual({ done: true, value: undefined });
  });

  it("fails only the refused subscription", async () => {
    const h = harness();
    const spy = h.stream.gexBook("SPY");
    const qqq = new Reader(h.stream.gexBook("QQQ"));
    await waitFor(() => h.sockets.length === 1 && subs(h.sockets[0]!).length === 2);
    const socket = h.sockets[0]!;
    socket.control({
      op: "nack", id: "sdk-1", code: "quota_exceeded", reason: "subscription_limit", limit: 50,
    });
    const error = await spy.next().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(StreamError);
    expect(error).toMatchObject({
      code: "quota_exceeded", reason: "subscription_limit", details: { limit: 50 },
    });
    socket.bytes(raw(KEYFRAME_HEX.replace("535059", "515151")));
    const state: GexBookState = await qqq.next();
    expect(state.symbol).toBe("QQQ");
  });

  it("recovers from a stream that can be subscribed to again after a terminal error", async () => {
    const h = harness({
      plans: [[{ op: "nack", id: null, code: "not_entitled_tier", reason: "stream_preview" }]],
    });
    await h.stream.gexBook("SPY").next().catch(() => undefined);
    const again = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready(2);
    socket.bytes(raw(KEYFRAME_HEX));
    expect((await again.next()).version).toBe(7);
  });

  it("yields the filtered view after every applied frame", async () => {
    const h = harness();
    const reader = new Reader(h.stream.gexBook("SPY", { filter: nearest(2) }));
    const socket = await h.ready();
    socket.bytes(raw(KEYFRAME_HEX));
    const first = await reader.next();
    // Spot 601.25: 600 and 602.5 are both 1.25 away.
    expect(Array.from(first.strikes)).toEqual([600, 602.5]);
    expect(Array.from(first.callGex)).toEqual([250_000_000, 187_500_000]);
    expect(Array.from(first.gex)).toEqual([156_250_000, 140_625_000]);
    expect([first.version, first.spot, first.netGex, first.reason]).toEqual([
      7, 601.25, -1_500_000_000, "subscribe",
    ]);
    expect(Array.from(first.changed)).toEqual([0, 1]);
    socket.bytes(raw(UPDATE_HEX));
    const second = await reader.next();
    // The update touched book positions 1 (600) and 4 (610); only 600 is in view.
    expect(Array.from(second.strikes)).toEqual([600, 602.5]);
    expect(Array.from(second.changed)).toEqual([0]);
    expect(Array.from(second.gex)).toEqual([143_750_000, 140_625_000]);
  });

  it("swaps the filter on a live subscription without resubscribing", async () => {
    const h = harness();
    const book = h.stream.gexBook("SPY");
    const reader = new Reader(book);
    const socket = await h.ready();
    socket.bytes(raw(KEYFRAME_HEX));
    expect((await reader.next()).strikes).toHaveLength(5);

    book.setFilter(top(1, "put"));
    expect(book.filter).not.toBe(undefined);
    const swapped = await reader.next();
    expect(Array.from(swapped.strikes)).toEqual([595]);
    expect(Array.from(swapped.changed)).toEqual([0]);
    expect(swapped.version).toBe(7);
    expect(book.state!.strikes).toHaveLength(5);

    socket.bytes(raw(UPDATE_HEX));
    const next = await reader.next();
    expect([next.version, Array.from(next.strikes), Array.from(next.changed)]).toEqual([
      8, [595], [],
    ]);
    book.setFilter(all());
    expect((await reader.next()).strikes).toHaveLength(5);
    expect(subs(socket)).toHaveLength(1);
    expect(socket.sent.some((m) => m.op === "unsub" || m.op === "resync")).toBe(false);
    expect(() => book.setFilter("nearest" as never)).toThrow(TypeError);
  });

  it("does not decode the deprecated v1 book messages", async () => {
    const h = harness();
    const reader = new Reader(h.stream.gexBook("SPY"));
    const socket = await h.ready();
    socket.bytes(encode(streamWire.Frame.fromPartial({
      symbol: "SPY",
      gexBook: { bookId: 1n, version: 1, strikes: [600_000n], gex: [1], crc: 0 },
    })));
    expect(await reader.quiet()).toBe(true);
    expect(socket.sent.some((m) => m.op === "resync")).toBe(false);
    socket.bytes(raw(KEYFRAME_HEX));
    expect((await reader.next()).version).toBe(7);
  });

  it("closing the stream ends the books and stops the reconnect", async () => {
    const h = harness({ minReconnectMs: 30, maxReconnectMs: 30, random: () => 1 });
    const book = h.stream.gexBook("SPY");
    const first = await h.ready();
    first.close();
    h.stream.close();
    expect(await book.next()).toEqual({ done: true, value: undefined });
    await pause(80);
    expect(h.sockets).toHaveLength(1);
  });
});

describe("ITMClient stream wiring", () => {
  const clients: ITMClient[] = [];
  afterEach(() => {
    for (const client of clients.splice(0)) client.close();
  });

  function build(apiKey: string | undefined, token?: string) {
    const sockets: FakeSocket[] = [];
    const mints: Array<{ authorization: string | null; method: string; path: string }> = [];
    const responses: Array<() => Response> = [];
    const client = new ITMClient({
      apiKey,
      token,
      retries: 0,
      fetch: async (input, init) => {
        const url = new URL(String(input));
        mints.push({
          authorization: new Headers(init?.headers).get("authorization"),
          method: init?.method ?? "GET",
          path: url.pathname,
        });
        const next = responses.shift();
        return next ? next() : Response.json({ data: { ticket: "tk", exp: 1 } });
      },
      stream: {
        minReconnectMs: 0,
        maxReconnectMs: 0,
        webSocketFactory: (_url, protocols) => {
          const socket = new FakeSocket(protocols);
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      },
    });
    clients.push(client);
    return { client, sockets, mints, responses };
  }

  it("connects an itm_ key with protocol 8 and the client name", async () => {
    const { client, sockets, mints } = build("itm_test");
    const reader = new Reader(client.stream.gexBook("SPY"));
    await waitFor(() => sockets.length === 1 && subs(sockets[0]!).length === 1);
    expect(mints).toEqual([
      { authorization: "Bearer itm_test", method: "POST", path: "/v2/stream/ticket" },
    ]);
    expect(sockets[0]!.protocols).toEqual(["itmatrixhq.v1"]);
    expect(sockets[0]!.sent[0]).toEqual({
      op: "auth", ticket: "tk", proto: 8, encoding: "protobuf", client: CLIENT,
    });
    sockets[0]!.bytes(raw(KEYFRAME_HEX));
    expect((await reader.next()).version).toBe(7);
  });

  it("keeps protocol 3 for other credentials and refuses a book without minting", async () => {
    for (const { client, sockets, mints } of [
      build(undefined, "session-token"),
      build("not-an-itm-key"),
    ]) {
      const error = await client.stream.gexBook("SPY").next().catch((cause: unknown) => cause);
      expect(error).toMatchObject({ reason: "api_key_required", terminal: true });
      expect(mints).toHaveLength(0);
      client.stream.spot("SPY");
      await waitFor(() => sockets.length === 1 && subs(sockets[0]!).length === 1);
      expect(sockets[0]!.sent[0]).toEqual({
        op: "auth", ticket: "tk", proto: 3, encoding: "protobuf",
      });
      expect(sockets[0]!.protocols).toBeUndefined();
    }
  });

  it("waits out an HTTP 429 with Retry-After on the mint", async () => {
    const { client, sockets, mints, responses } = build("itm_test");
    responses.push(() =>
      Response.json(
        { error: { code: "rate_limited", message: "slow down" } },
        { status: 429, headers: { "retry-after": "0.15" } },
      )
    );
    const started = performance.now();
    client.stream.gexBook("SPY");
    await waitFor(() => sockets.length === 1 && subs(sockets[0]!).length === 1);
    expect(mints).toHaveLength(2);
    expect(performance.now() - started).toBeGreaterThanOrEqual(145);
  });

  it("treats HTTP 403 stream_preview on the mint as terminal", async () => {
    const { client, sockets, mints, responses } = build("itm_test");
    responses.push(() =>
      Response.json(
        {
          error: {
            code: "not_entitled_tier",
            message: "not on the preview",
            details: { reason: "stream_preview" },
          },
        },
        { status: 403 },
      )
    );
    const error = await client.stream.gexBook("SPY").next().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(StreamError);
    expect(error).toMatchObject({ reason: "stream_preview", terminal: true, status: 403 });
    await pause(50);
    expect([mints.length, sockets.length]).toEqual([1, 0]);
  });
});
