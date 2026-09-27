import { describe, expect, it } from "vitest";
import { ITMClient, ITMError, restWire } from "../src/index.js";

describe("thin REST client", () => {
  it("uses direct methods, bearer auth, and ordinary JSON", async () => {
    const client = new ITMClient({
      apiKey: "itm_test",
      fetch: async (input, init) => {
        const url = new URL(String(input));
        expect(url.pathname).toBe("/v2/gex/SPY/grid");
        expect(url.searchParams.get("top")).toBe("5");
        expect(new Headers(init?.headers).get("authorization")).toBe("Bearer itm_test");
        expect(new Headers(init?.headers).get("accept")).toBe("application/json");
        return Response.json(
          { data: { spot: 600, net_gex: 12, max_abs_gex: 9, strikes: [] },
            meta: { plane: "public" } },
          { headers: { "x-request-id": "req-json" } },
        );
      },
    });

    const result = await client.gex("SPY", { top: 5 });
    expect(result.data.spot).toBe(600);
    expect(result.meta.plane).toBe("public");
    expect(result.requestId).toBe("req-json");
  });

  it("negotiates protobuf and preserves uint64 values", async () => {
    const body = restWire.BarsResponse.encode({
      priceScale: 10_000,
      bars: [{ tsMs: 5_000_000_000n, o: 1, h: 2, l: 1, c: 2, v: 9 }],
      cursor: "next",
    }).finish();
    const client = new ITMClient({
      transport: "protobuf",
      fetch: async (_input, init) => {
        expect(new Headers(init?.headers).get("accept")).toBe("application/x-protobuf");
        return new Response(body, {
          headers: {
            "content-type": "application/x-protobuf",
            "x-itm-plane": "public",
            "x-itm-caps": '["bars"]',
          },
        });
      },
    });

    const result = await client.bars("SPY", {
      from: "2026-08-29T13:30:00Z",
      to: "2026-08-29T20:00:00Z",
    });
    expect(result.data.bars[0]?.tsMs).toBe(5_000_000_000n);
    expect(result.meta.caps).toEqual(["bars"]);
  });

  it("negotiates the typed off-exchange protobuf body", async () => {
    const body = restWire.OffExchangeActivityResponse.encode({
      rows: [{ date: "2026-09-25", offExchangeVolume: 100, notional: 1000,
        tradeCount: 3n, averageSize: 33.3, vwap: 10 }],
    }).finish();
    const client = new ITMClient({
      transport: "protobuf",
      fetch: async (input, init) => {
        expect(new URL(String(input)).pathname).toBe("/v2/offexchange/SPY/activity");
        expect(new Headers(init?.headers).get("accept")).toBe("application/x-protobuf");
        return new Response(body, { headers: { "content-type": "application/x-protobuf" } });
      },
    });
    const result = await client.offexchange.activity("spy");
    expect("rows" in result.data && result.data.rows[0]?.tradeCount).toBe(3n);
  });

  it("exposes one stable API error", async () => {
    const client = new ITMClient({
      retries: 0,
      fetch: async () => new Response(
        JSON.stringify({ error: { code: "rate_limited", message: "slow down" } }),
        {
          status: 429,
          headers: {
            "content-type": "application/json",
            "retry-after": "3",
            "x-request-id": "req-error",
          },
        },
      ),
    });

    await expect(client.bars("SPY", { from: "2026-08-29", to: "2026-08-30" }))
      .rejects.toMatchObject({
        name: "ITMError",
        status: 429,
        code: "rate_limited",
        requestId: "req-error",
        retryAfterSeconds: 3,
      } satisfies Partial<ITMError>);
  });

  it("accepts only the json and protobuf transports", () => {
    // @ts-expect-error any other transport name is absent from the public type.
    expect(() => new ITMClient({ transport: "binary" })).toThrow();
  });
});
