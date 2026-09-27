import { describe, expect, it } from "vitest";
import { ITMClient, restWire, type MarketDataCodec } from "../src/index.js";

const binary = (body: Uint8Array) => new Response(Uint8Array.from(body).buffer, {
  headers: { "content-type": "application/x-protobuf", "x-itm-plane": "public" },
});

describe("domain model methods", () => {
  it("normalizes protobuf option identity, dates, missing greeks, and capture time", async () => {
    const client = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.ChainResponse.encode(restWire.ChainResponse.create({
        capturedAtMs: 1788000000000n, spot: 600, partial: true,
        rows: [{ expiryEpochDay: 0, right: 1, strikeThousandths: 600000n,
          oi: 42n, sharesPerContract: 100 }],
      })).finish(),
    ) });
    const result = await client.getOptionChain(" spy ");
    expect(result.data.captured_at).toBe(1788000000000);
    expect(result.data.rows[0]).toMatchObject({
      contract: { expiry: "1970-01-01", right: "put", strike: 600000 },
      oi: 42, shares_per_contract: 100,
    });
    expect(result.data.rows[0]?.gamma).toBeUndefined();
    expect(result.data.partial).toBe(true);
  });

  it("returns the same GEX model for JSON and protobuf (including zero-valued optionals)", async () => {
    const data = { spot: 600, prior_close_spot: null, captured_at: null,
      net_gex: 4, net_gex_shares: null, flip_point: null, max_abs_gex: 4,
      max_abs_gex_shares: null,
      strikes: [{ strike: 600000, gex: 4, gex_shares: null, gex_0dte: 0,
        delta_adj: null, delta_adj_shares: null }] };
    const json = new ITMClient({ fetch: async () => Response.json({ data, meta: {} }) });
    const proto = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.GexGridResponse.encode(restWire.GexGridResponse.create({ spot: 600,
        netGex: 4, maxAbsGex: 4, rows: [{ strikeThousandths: 600000n, gex: 4, gex0dte: 0 }],
      })).finish(),
    ) });
    expect((await proto.getGex("SPY")).data).toEqual((await json.getGex("SPY")).data);
  });

  it("carries the GEX share siblings identically over JSON and protobuf", async () => {
    const data = { spot: 600, prior_close_spot: 598, captured_at: 1788000000000,
      net_gex: 5e8, net_gex_shares: 9.5e5, flip_point: 601, max_abs_gex: 6e8,
      max_abs_gex_shares: 1.1e6,
      strikes: [
        { strike: 600000, gex: 6e8, gex_shares: 1.1e6, gex_0dte: 2e8, gex_0dte_shares: 3.3e5,
          delta_adj: 1.5e8, delta_adj_shares: 2.5e5, expiry: "1970-01-02" },
        { strike: 590000, gex: -1e8, gex_shares: -1.5e5, delta_adj: null,
          delta_adj_shares: null, expiry: "1970-01-02" },
      ] };
    const json = new ITMClient({ fetch: async () => Response.json({ data, meta: {} }) });
    const proto = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.GexGridResponse.encode(restWire.GexGridResponse.create({ spot: 600,
        priorCloseSpot: 598, capturedAtMs: 1788000000000n, netGex: 5e8, netGexShares: 9.5e5,
        flipPoint: 601, maxAbsGex: 6e8, maxAbsGexShares: 1.1e6, rows: [
          { strikeThousandths: 600000n, gex: 6e8, gexShares: 1.1e6, gex0dte: 2e8,
            gex0dteShares: 3.3e5, deltaAdj: 1.5e8, deltaAdjShares: 2.5e5, expiryEpochDay: 1 },
          { strikeThousandths: 590000n, gex: -1e8, gexShares: -1.5e5, expiryEpochDay: 1 },
        ] })).finish(),
    ) });
    const fromProto = (await proto.getGex("SPY")).data;
    expect(fromProto).toEqual((await json.getGex("SPY")).data);
    expect(fromProto.net_gex_shares).toBe(9.5e5);
    expect(fromProto.strikes[0]?.gex_shares).toBe(1.1e6);
    expect(fromProto.strikes[1]?.delta_adj_shares).toBeNull();
    expect(fromProto.strikes[1]?.gex_0dte_shares).toBeUndefined();
  });

  it("keeps unknown GEX shares null on protobuf rather than zero", async () => {
    const proto = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.GexGridResponse.encode(restWire.GexGridResponse.create({ netGex: 1,
        maxAbsGex: 1, rows: [{ strikeThousandths: 600000n, gex: 1, deltaAdj: 0.5 }] })).finish(),
    ) });
    const grid = (await proto.getGex("SPY")).data;
    expect(grid.net_gex_shares).toBeNull();
    expect(grid.max_abs_gex_shares).toBeNull();
    expect(grid.strikes[0]?.gex_shares).toBeNull();
    expect(grid.strikes[0]?.delta_adj_shares).toBeNull();
  });

  it("types the GEX history captures with their share fields", async () => {
    const data = { symbol: "SPY", session_date: "2026-09-24", captures: [
      { captured_at: 1, spot: 500, prior_close_spot: null, net_gex: 1e9, net_gex_shares: 2e6,
        flip_point: null, strikes: [{ strike: 500000, gex: 1e9, gex_shares: 2e6,
          delta_adj: null, delta_adj_shares: null }] },
    ] };
    const client = new ITMClient({ fetch: async () => Response.json({ data, meta: {} }) });
    const result = await client.getGexHistory("SPY", { date: "2026-09-24" });
    expect(result.data.captures[0]?.net_gex_shares).toBe(2e6);
    expect(result.data.captures[0]?.strikes[0]?.gex_shares).toBe(2e6);
  });

  it("unscales bar prices and preserves pagination metadata", async () => {
    const client = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.BarsResponse.encode(restWire.BarsResponse.create({ priceScale: 10000,
        cursor: "page2", bars: [{ tsMs: 5000000000n, o: 6000000, h: 6100000,
          l: 5900000, c: 6050000, v: 0, vwap: 6030000 }],
      })).finish(),
    ) });
    const result = await client.getBars("spy", { from: "2026-08-01", to: "2026-08-02", timeframe: "5m" });
    expect(result.data[0]).toMatchObject({ symbol: "SPY", timeframe: "5m", open: 600,
      high: 610, low: 590, close: 605, volume: 0, vwap: 603, ts_ms: 5000000000 });
    expect(result.meta.cursor).toBe("page2");
  });

  it("normalizes replay tick sources and preserves session and sequence", async () => {
    const client = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.ReplayResponse.encode(restWire.ReplayResponse.create({ priceScale: 10000,
        fidelity: 0, complete: true, sources: ["edgx_stocks"],
        ticks: [{ tsMs: 1788000000000n, priceScaled: 6000000, sourceId: 0, seq: 5000000000n }],
      })).finish(),
    ) });
    const result = await client.getReplay("SPY", { date: "2026-08-29" });
    expect(result.data).toEqual({ session: "2026-08-29", fidelity: "ticks", complete: true,
      events: [[1788000000000, 600, "edgx_stocks", 5000000000]] });
  });

  it("accepts JSON fallback on a protobuf request", async () => {
    const data = { spot: null, captured_at: null, partial: false, rows: [] };
    const client = new ITMClient({ transport: "protobuf",
      fetch: async () => Response.json({ data, meta: { plane: "db" } }) });
    expect((await client.getOptionChain("SPY")).data).toEqual(data);
  });

  it("fails rather than silently rounding oversized wire integers", async () => {
    const client = new ITMClient({ transport: "protobuf", fetch: async () => binary(
      restWire.ChainResponse.encode(restWire.ChainResponse.create({ rows: [
        { oi: 9007199254740993n, sharesPerContract: 100 },
      ] })).finish(),
    ) });
    await expect(client.getOptionChain("SPY")).rejects.toThrow("safe range");
  });

  it("allows an application-owned codec without adding a private transport mode", async () => {
    const data = { spot: null, captured_at: null, prior_close_spot: null,
      net_gex: 0, flip_point: null, max_abs_gex: 0, strikes: [] };
    const codec = { mediaType: "application/example", decode: () => ({ data }) } as MarketDataCodec;
    const client = new ITMClient({ codec, fetch: async (_url, init) => {
      expect(new Headers(init?.headers).get("accept")).toBe(codec.mediaType);
      return new Response(new Uint8Array(), { headers: { "content-type": codec.mediaType } });
    } });
    expect((await client.getGex("SPY")).data).toEqual(data);
  });

  it("looks up typed ticker guesses and keeps response metadata", async () => {
    const client = new ITMClient({ fetch: async (url) => {
      expect(new URL(String(url)).pathname).toBe("/v2/symbols/lookup");
      expect(new URL(String(url)).searchParams.get("q")).toBe("spy");
      return Response.json({ data: { valid: true, symbol: "SPY", name: "SPDR", class: "equity" }, meta: {} });
    } });
    expect((await client.lookupSymbol(" spy ")).data.valid).toBe(true);
  });
});

describe("credential confinement", () => {
  it.each(["https://evil.invalid/v2/quotes", "//evil.invalid/v2/quotes", "/v2/../private", "/v2/%2e%2e/private", "/v2/quotes?x=1", "/v2/\\evil"])(
    "rejects unsafe path %s before resolving credentials", async (path) => {
      let resolved = false;
      const client = new ITMClient({ apiKey: () => { resolved = true; return "secret"; } });
      await expect(client.request(path)).rejects.toThrow("request path");
      expect(resolved).toBe(false);
    },
  );
  it("disables redirects and sets a finite request timeout", async () => {
    const client = new ITMClient({ fetch: async (_url, init) => {
      expect(init?.redirect).toBe("error");
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      return Response.json({ data: [], meta: {} });
    } });
    await client.listSymbols();
  });
});
