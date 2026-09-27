import type { Frame } from "./_wire/ws.js";
import { ITMError } from "./errors.js";

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

export type Topic = "stocks" | "spot" | "index" | "chain" | "gex";

export interface SubscribeOptions {
  symbol: string;
  want?: readonly string[];
  timeframe?: string;
  expiry?: string;
  strikeGte?: number;
  strikeLte?: number;
  expiries?: string | readonly string[];
  byExpiry?: boolean;
  component?: string;
}

export interface StreamConfig {
  url: string;
  ticket: () => Promise<{ ticket: string; exp: number }>;
  webSocketFactory?: (url: string) => WebSocket;
  maxQueue?: number;
  minReconnectMs?: number;
  maxReconnectMs?: number;
}

interface Control {
  op?: string;
  id?: string;
  code?: string;
  message?: string;
  effective?: unknown;
  reconnect_after_ms?: number;
  t?: unknown;
}

interface Pending {
  resolve: (result: IteratorResult<Frame>) => void;
  reject: (error: unknown) => void;
}

class FrameQueue {
  private readonly frames: Frame[] = [];
  private readonly pending: Pending[] = [];
  private ended = false;
  private error: unknown;

  constructor(
    private readonly maximum: number,
    private readonly overflow: () => void,
  ) {}

  push(frame: Frame): void {
    if (this.ended) return;
    const waiter = this.pending.shift();
    if (waiter) return waiter.resolve({ value: frame, done: false });
    if (this.frames.length >= this.maximum) {
      this.fail(new ITMError({
        status: 0,
        code: "unknown",
        message: `stream consumer fell behind (queue limit ${this.maximum})`,
      }));
      this.overflow();
      return;
    }
    this.frames.push(frame);
  }

  next(): Promise<IteratorResult<Frame>> {
    const frame = this.frames.shift();
    if (frame) return Promise.resolve({ value: frame, done: false });
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

export class StreamSubscription implements AsyncIterableIterator<Frame> {
  effective?: unknown;
  readonly id: string;
  readonly topic: Topic;
  readonly options: SubscribeOptions;
  private readonly queue: FrameQueue;
  private active = true;

  constructor(
    id: string,
    topic: Topic,
    options: SubscribeOptions,
    maximum: number,
    private readonly stop: (subscription: StreamSubscription) => void,
  ) {
    this.id = id;
    this.topic = topic;
    this.options = options;
    this.queue = new FrameQueue(maximum, () => this.close());
  }

  next(): Promise<IteratorResult<Frame>> {
    return this.queue.next();
  }

  return(): Promise<IteratorResult<Frame>> {
    this.close();
    return Promise.resolve({ value: undefined, done: true });
  }

  [Symbol.asyncIterator](): AsyncIterableIterator<Frame> {
    return this;
  }

  feed(frame: Frame): void {
    if (this.active && matches(this, frame)) this.queue.push(frame);
  }

  fail(error: unknown): void {
    this.active = false;
    this.queue.fail(error);
  }

  finish(): void {
    this.active = false;
    this.queue.end();
  }

  private close(): void {
    if (!this.active) return;
    this.active = false;
    this.queue.end();
    this.stop(this);
  }
}

let nextId = 1;

export class Stream {
  private readonly subscriptions = new Map<string, StreamSubscription>();
  private readonly factory: (url: string) => WebSocket;
  private decodeFrame?: (bytes: Uint8Array) => Frame;
  private socket?: WebSocket;
  private connecting = false;
  private authed = false;
  private closed = false;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private reconnectAfterMs?: number;
  private delayMs: number;

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
    this.factory = config.webSocketFactory ?? defaultWebSocket;
    this.delayMs = config.minReconnectMs ?? 250;
  }

  subscribe(topic: Topic, options: SubscribeOptions): StreamSubscription {
    if (!options.symbol) throw new TypeError("symbol is required");
    const subscription = new StreamSubscription(
      `sdk-${nextId++}`,
      topic,
      { ...options, symbol: options.symbol.toUpperCase() },
      this.config.maxQueue ?? 64,
      (value) => this.remove(value),
    );
    this.closed = false;
    this.subscriptions.set(subscription.id, subscription);
    if (this.authed) this.sendSubscription(subscription);
    else void this.connect();
    return subscription;
  }

  stocks(symbol: string, want: readonly string[] = ["trades", "mid", "bars", "quote"]):
    StreamSubscription {
    return this.subscribe("stocks", { symbol, want });
  }

  spot(symbol: string): StreamSubscription {
    return this.subscribe("spot", { symbol });
  }

  gex(symbol: string, options: Omit<SubscribeOptions, "symbol"> = {}):
    StreamSubscription {
    return this.subscribe("gex", { ...options, symbol });
  }

  chain(symbol: string, options: Omit<SubscribeOptions, "symbol"> = {}):
    StreamSubscription {
    return this.subscribe("chain", { ...options, symbol });
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    for (const subscription of this.subscriptions.values()) subscription.finish();
    this.subscriptions.clear();
    this.socket?.close(1000, "stream closed");
    this.socket = undefined;
    this.authed = false;
  }

  private async connect(): Promise<void> {
    if (
      this.closed || this.connecting || this.socket !== undefined ||
      this.subscriptions.size === 0
    ) return;
    this.connecting = true;
    try {
      // The codec is resolved before the socket exists, so no frame can
      // arrive while the import is still in flight.
      const [{ ticket }, wire] = await Promise.all([
        this.config.ticket(), loadFrameCodec(),
      ]);
      this.decodeFrame = wire.Frame.decode;
      if (this.closed || this.subscriptions.size === 0) return;
      const socket = this.factory(this.config.url);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      socket.addEventListener("open", () => {
        socket.send(JSON.stringify({
          op: "auth", ticket, proto: 3, encoding: "protobuf",
        }));
      });
      socket.addEventListener("message", (event) => void this.message(socket, event));
      socket.addEventListener("close", () => this.disconnected(socket));
    } catch {
      this.scheduleReconnect();
    } finally {
      this.connecting = false;
    }
  }

  private async message(socket: WebSocket, event: MessageEvent): Promise<void> {
    if (socket !== this.socket) return;
    if (typeof event.data === "string") {
      this.control(socket, event.data);
      return;
    }
    try {
      const bytes = await messageBytes(event.data);
      const decode = this.decodeFrame ?? (await loadFrameCodec()).Frame.decode;
      const frame = decode(bytes);
      for (const subscription of this.subscriptions.values()) subscription.feed(frame);
    } catch (cause) {
      const error = new ITMError({
        status: 0,
        code: "unknown",
        message: "invalid protobuf websocket frame",
        details: { cause: String(cause) },
      });
      for (const subscription of this.subscriptions.values()) subscription.fail(error);
      this.subscriptions.clear();
      socket.close(1002, "invalid protobuf frame");
    }
  }

  private control(socket: WebSocket, raw: string): void {
    let frame: Control;
    try {
      frame = JSON.parse(raw) as Control;
    } catch {
      socket.close(1002, "invalid control frame");
      return;
    }
    switch (frame.op) {
      case "authed":
        this.authed = true;
        this.delayMs = this.config.minReconnectMs ?? 250;
        for (const subscription of this.subscriptions.values()) {
          this.sendSubscription(subscription);
        }
        break;
      case "ack": {
        const subscription = frame.id ? this.subscriptions.get(frame.id) : undefined;
        if (subscription) subscription.effective = frame.effective;
        break;
      }
      case "nack": {
        const subscription = frame.id ? this.subscriptions.get(frame.id) : undefined;
        if (subscription) {
          subscription.fail(new ITMError({
            status: 0,
            code: sdkCode(frame.code),
            message: frame.message ?? "stream subscription rejected",
          }));
          this.subscriptions.delete(subscription.id);
        }
        break;
      }
      case "ping":
        socket.send(JSON.stringify({ op: "pong", t: frame.t }));
        break;
      case "bye":
        if (
          typeof frame.reconnect_after_ms === "number" &&
          frame.reconnect_after_ms >= 0
        ) this.reconnectAfterMs = frame.reconnect_after_ms;
        socket.close(1000, "server requested reconnect");
        break;
    }
  }

  private sendSubscription(subscription: StreamSubscription): void {
    if (!this.socket || this.socket.readyState !== 1 || !this.authed) return;
    const options = subscription.options;
    this.socket.send(JSON.stringify({
      op: "sub",
      id: subscription.id,
      topic: subscription.topic,
      symbol: options.symbol,
      want: options.want,
      timeframe: options.timeframe,
      expiry: options.expiry,
      strike_gte: options.strikeGte,
      strike_lte: options.strikeLte,
      expiries: options.expiries,
      by_expiry: options.byExpiry,
      component: options.component,
    }));
  }

  private remove(subscription: StreamSubscription): void {
    if (!this.subscriptions.delete(subscription.id)) return;
    if (this.socket?.readyState === 1 && this.authed) {
      this.socket.send(JSON.stringify({ op: "unsub", id: subscription.id }));
    }
    if (this.subscriptions.size === 0) {
      this.socket?.close(1000, "no subscriptions");
      this.socket = undefined;
      this.authed = false;
    }
  }

  private disconnected(socket: WebSocket): void {
    if (socket !== this.socket) return;
    this.socket = undefined;
    this.authed = false;
    if (!this.closed && this.subscriptions.size > 0) this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer || this.subscriptions.size === 0) return;
    const wait = this.reconnectAfterMs ?? this.delayMs;
    this.reconnectAfterMs = undefined;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      void this.connect();
    }, wait);
    this.delayMs = Math.min(
      Math.max(this.delayMs * 2, this.config.minReconnectMs ?? 250),
      this.config.maxReconnectMs ?? 10_000,
    );
  }
}

function matches(subscription: StreamSubscription, frame: Frame): boolean {
  if (frame.symbol !== subscription.options.symbol) return false;
  switch (subscription.topic) {
    case "stocks":
      return !!(frame.trade || frame.mid || frame.barOpen || frame.barClose || frame.quote);
    case "spot":
      return frame.spot !== undefined;
    case "index":
      return frame.indexValue !== undefined;
    case "gex":
      return !!(frame.gexSnap || frame.gexDelta);
    case "chain":
      return !!(frame.chainSnap || frame.chainDelta);
  }
}

async function messageBytes(data: unknown): Promise<Uint8Array> {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    return new Uint8Array(await data.arrayBuffer());
  }
  throw new TypeError("unsupported websocket message type");
}

function defaultWebSocket(url: string): WebSocket {
  if (!globalThis.WebSocket) {
    throw new TypeError("no WebSocket implementation is available");
  }
  return new globalThis.WebSocket(url);
}

function sdkCode(value?: string): ITMError["code"] {
  const known = new Set([
    "unauthenticated", "token_expired", "invalid_ticket", "not_entitled_tier",
    "not_entitled_symbol", "attestation_required", "rate_limited",
    "quota_exceeded", "invalid_params", "not_found", "range_capped",
    "partial_data", "internal",
  ]);
  return known.has(value ?? "") ? value as ITMError["code"] : "unknown";
}
