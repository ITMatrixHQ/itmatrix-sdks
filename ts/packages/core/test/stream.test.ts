import { describe, expect, it } from "vitest";
import { Stream, streamWire } from "../src/index.js";

type Listener = (event: Event | MessageEvent) => void;

class FakeSocket {
  binaryType = "";
  readyState = 0;
  readonly sent: Array<Record<string, unknown>> = [];
  private readonly listeners = new Map<string, Listener[]>();

  constructor() {
    queueMicrotask(() => {
      this.readyState = 1;
      this.emit("open", {} as Event);
    });
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  send(raw: string): void {
    const control = JSON.parse(raw) as Record<string, unknown>;
    this.sent.push(control);
    if (control.op === "auth") {
      queueMicrotask(() => this.control({ op: "authed" }));
    }
  }

  close(_code?: number, _reason?: string): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit("close", {} as Event);
  }

  control(value: Record<string, unknown>): void {
    this.emit("message", { data: JSON.stringify(value) } as MessageEvent);
  }

  frame(value: streamWire.Frame): void {
    this.emit("message", {
      data: streamWire.Frame.encode(value).finish(),
    } as MessageEvent);
  }

  private emit(type: string, event: Event | MessageEvent): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error("condition was not reached");
}

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
    expect(socket.sent[0]).toEqual({
      op: "auth", ticket: "ticket-1", proto: 3, encoding: "protobuf",
    });
    expect(socket.sent).toContainEqual({ op: "pong", t: 42 });

    await stocks.return();
    await spot.return();
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
  });
});
