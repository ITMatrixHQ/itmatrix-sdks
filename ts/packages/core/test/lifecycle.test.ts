import { describe, expect, it } from "vitest";
import { DEFAULT_BASE_URL, ITMClient } from "../src/index.js";

const grid = { data: { spot: 600, net_gex: 1, max_abs_gex: 1, strikes: [] } };

class IdleSocket {
  binaryType = "";
  readyState = 0;
  closes = 0;
  addEventListener(): void {}
  send(): void {}
  close(): void {
    this.closes += 1;
    this.readyState = 3;
  }
}

describe("client lifecycle", () => {
  it("needs no disposal for REST and defaults to the one base URL", async () => {
    const seen: string[] = [];
    const client = new ITMClient({
      fetch: async (input) => {
        seen.push(String(input));
        return Response.json(grid);
      },
    });
    expect(client.baseUrl).toBe(DEFAULT_BASE_URL);
    expect((await client.getGex("SPY")).data.net_gex).toBe(1);
    expect(seen[0]?.startsWith(`${DEFAULT_BASE_URL}/v2/gex/SPY/grid`)).toBe(true);
    expect(client.isClosed).toBe(false);
  });

  it("close is idempotent, ends the stream, and later use is an error", async () => {
    const sockets: IdleSocket[] = [];
    const client = new ITMClient({
      fetch: async () => Response.json({ data: { ticket: "t", exp: 0 } }),
      stream: {
        webSocketFactory: () => {
          const socket = new IdleSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      },
    });
    const subscription = client.stream.spot("SPY");
    for (let i = 0; i < 20 && sockets.length === 0; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    expect(sockets).toHaveLength(1);
    client.close();
    client.close();
    expect(client.isClosed).toBe(true);
    expect(sockets[0]?.closes).toBe(1);
    await expect(subscription.next()).resolves.toEqual({ done: true, value: undefined });
    await expect(client.getGex("SPY")).rejects.toThrow(/ITMClient is closed/);
    await expect(client.request("/v2/me")).rejects.toThrow(/ITMClient is closed/);
    expect(() => client.stream).toThrow(/ITMClient is closed/);
  });

  it("closing a REST-only client builds no stream", () => {
    const client = new ITMClient({ fetch: async () => Response.json(grid) });
    client.close();
    expect(client.isClosed).toBe(true);
  });
});
