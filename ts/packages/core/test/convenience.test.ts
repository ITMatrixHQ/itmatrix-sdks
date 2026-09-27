import { describe, expect, it } from "vitest";
import { analyzeGex, ITMClient, resolveBarWindow, validateBarWindow,
  type GexGrid } from "../src/index.js";

describe("client-side convenience", () => {
  it("ranks visible GEX strikes without recomputing server scalars", () => {
    const grid: GexGrid = {
      spot: 602, prior_close_spot: null, captured_at: null,
      net_gex: 123, max_abs_gex: 50, flip_point: null,
      strikes: [
        { strike: 600_000, gex: 7, delta_adj: null, expiry: "2026-09-27" },
        { strike: 600_000, gex: -2, delta_adj: null, expiry: "2026-10-02" },
        { strike: 610_000, gex: -9, delta_adj: null },
        { strike: 590_000, gex: 5, delta_adj: null },
      ],
    };
    const result = analyzeGex(grid, { levels: 2 });
    expect(result.netGex).toBe(123);
    expect(result.zeroGamma).toBeNull();
    expect(result.positiveLevels.map(row => row.strike)).toEqual([590, 600]);
    expect(result.positiveLevels[1]?.gex).toBe(5);
    expect(result.negativeLevels[0]?.gex).toBe(-9);
    expect(result.positiveLevels[0]?.distanceFromSpot).toBe(-12);
    expect(result.levelsScope).toBe("returned_rows");
  });

  it("resolves NY dates, leap months, and prevents a clipped minute read", () => {
    const now = new Date("2026-09-27T01:00:00Z");
    expect(resolveBarWindow("today", { now }).from).toBe("2026-09-26");
    const month = resolveBarWindow("calendar_month", { month: "2024-02", now });
    expect(month).toMatchObject({ from: "2024-02-01", to: "2024-02-29", days: 29 });
    expect(() => validateBarWindow(
      resolveBarWindow("calendar_month", { month: "2026-09", now }), "1m",
    )).toThrow(/cannot cover/);
  });

  it("composes one GEX fetch and preserves response metadata", async () => {
    const client = new ITMClient({ fetch: async input => {
      expect(new URL(String(input)).pathname).toBe("/v2/gex/SPY/grid");
      return Response.json({
        data: { strikes: [{ strike: 600_000, gex: 5 }], net_gex: -10,
          max_abs_gex: 5, spot: 601, flip_point: 600.5 },
        meta: { date: "2026-09-26" },
      }, { headers: { "x-request-id": "convenience-1" } });
    } });
    const result = await client.getGexAnalysis("spy");
    expect(result.data.netGex).toBe(-10);
    expect(result.data.regime).toBe("negative");
    expect(result.data.zeroGamma).toBe(600.5);
    expect(result.meta.date).toBe("2026-09-26");
    expect(result.requestId).toBe("convenience-1");
  });

  it("requests all dates in one calendar month with daily bars", async () => {
    const client = new ITMClient({ fetch: async input => {
      const url = new URL(String(input));
      expect(url.pathname).toBe("/v2/stocks/SPY/bars");
      expect(url.searchParams.get("from")).toBe("2024-02-01");
      expect(url.searchParams.get("to")).toBe("2024-02-29");
      expect(url.searchParams.get("timeframe")).toBe("1d");
      expect(url.searchParams.get("tz")).toBe("America/New_York");
      return Response.json({ data: [], meta: {} });
    } });
    const result = await client.getBarsForPeriod("SPY", "calendar_month", { month: "2024-02" });
    expect(result.data).toEqual([]);
  });
});
