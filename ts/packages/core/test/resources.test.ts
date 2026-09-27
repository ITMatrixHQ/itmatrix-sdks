import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ITMClient,
  INTENTIONALLY_UNSUPPORTED_OPERATIONS,
  SUPPORTED_OPERATIONS,
  type JsonObject,
} from "../src/index.js";

interface Operation {
  operationId?: string;
  "x-exposure"?: string;
}

describe("contract completeness", () => {
  it("covers every non-internal /v2 operation in the pinned contract", () => {
    const spec = JSON.parse(readFileSync(
      resolve(process.cwd(), "../../../spec/openapi.json"),
      "utf8",
    )) as { paths: Record<string, Record<string, Operation>> };
    const expected = Object.entries(spec.paths)
      .filter(([path]) => path.startsWith("/v2/"))
      .flatMap(([, methods]) => Object.values(methods))
      .filter((operation) =>
        operation.operationId !== undefined && operation["x-exposure"] !== "internal"
      )
      .map((operation) => operation.operationId!)
      .sort();

    expect([...SUPPORTED_OPERATIONS, ...INTENTIONALLY_UNSUPPORTED_OPERATIONS].sort()).toEqual(expected);
    expect(expected.length).toBeGreaterThan(0);
  });
});

describe("grouped resources", () => {
  it("maps constrained flow options to stable wire names", async () => {
    const calls: URL[] = [];
    const client = new ITMClient({
      fetch: async (input) => {
        calls.push(new URL(String(input)));
        return Response.json({ data: {}, meta: {} });
      },
    });

    await client.flow.largeTrades({
      minPremiumUsd: 250_000,
      session: "2026-09-26",
      limit: 25,
    });
    await client.flow.eod({
      session: "2026-09-25",
      symbol: "spy",
      right: "call",
    });
    const premium = await client.flow.premium("spy", {
      sessions: 2,
      bucket: "5m",
      minPremiumUsd: 100_000,
      dte: "zero",
      toMs: 1_790_000_000_000,
    });

    expect(calls[0]?.pathname).toBe("/v2/flow/large-trades");
    expect(calls[0]?.searchParams.get("min_premium_usd")).toBe("250000");
    expect(calls[0]?.searchParams.get("limit")).toBe("25");
    expect(calls[1]?.pathname).toBe("/v2/flow/eod");
    expect(calls[1]?.searchParams.get("session")).toBe("2026-09-25");
    expect(calls[1]?.searchParams.get("symbol")).toBe("SPY");
    expect(calls[1]?.searchParams.get("right")).toBe("call");
    expect(calls[2]?.pathname).toBe("/v2/flow/premium");
    expect(calls[2]?.searchParams.get("symbol")).toBe("SPY");
    expect(calls[2]?.searchParams.get("sessions")).toBe("2");
    expect(calls[2]?.searchParams.get("bucket")).toBe("5m");
    expect(calls[2]?.searchParams.get("min_premium_usd")).toBe("100000");
    expect(calls[2]?.searchParams.get("dte")).toBe("zero");
    expect(calls[2]?.searchParams.get("to_ms")).toBe("1790000000000");
    expect(premium.data).toEqual({});
  });

  it("requests an exact immutable GEX reference book", async () => {
    const calls: URL[] = [];
    const client = new ITMClient({
      fetch: async (input) => {
        calls.push(new URL(String(input)));
        return Response.json({
          data: {
            symbol: "SPY",
            basis: "prev_close",
            session_date: "2026-09-25",
            reference_session_date: "2026-09-24",
            captured_at: null,
            spot: null,
            net_gex: null,
            complete: null,
            contract_count: null,
            gamma_absent: null,
            strikes: [],
          },
          meta: { available: false },
        });
      },
    });

    const result = await client.getGexReference("spy", {
      date: "2026-09-25",
      basis: "prev_close",
    });

    expect(calls[0]?.pathname).toBe("/v2/gex/SPY/reference");
    expect(calls[0]?.searchParams.get("date")).toBe("2026-09-25");
    expect(calls[0]?.searchParams.get("basis")).toBe("prev_close");
    expect(result.data.captured_at).toBeNull();
    expect(result.data.strikes).toEqual([]);
  });

  it("keeps CRUD, text imports, reference data, and corrected expirations readable",
    async () => {
      const calls: Array<{ url: URL; init?: RequestInit }> = [];
      const client = new ITMClient({
        fetch: async (input, init) => {
          const url = new URL(String(input));
          calls.push({ url, init });
          if (url.pathname.endsWith(".proto")) {
            return new Response('syntax = "proto3";', {
              headers: { "content-type": "text/plain" },
            });
          }
          return Response.json({ data: { ok: true }, meta: {} });
        },
      });

      await client.account.submitClassification("non_professional");
      await client.journal.importTrades('{"symbol":"SPY"}\n', {
        importId: "import-1",
      });
      await client.reference.get("spy");
      await client.expirations("spy", {
        at: "2026-08-29",
        expiry: "2026-09-18",
        strikeGte: 500_000,
      });
      const schema = await client.protocols.websocketSchema();

      expect(calls[0]?.url.pathname).toBe("/v2/account/classification");
      expect(calls[0]?.init?.method).toBe("POST");
      expect(calls[0]?.init?.body).toBe('{"classification":"non_professional"}');

      expect(calls[1]?.url.searchParams.get("import_id")).toBe("import-1");
      expect(new Headers(calls[1]?.init?.headers).get("content-type")).toBe("text/plain");
      expect(calls[1]?.init?.body).toBe('{"symbol":"SPY"}\n');

      expect(calls[2]?.url.pathname).toBe("/v2/symbols/SPY");
      expect(calls[3]?.url.pathname).toBe("/v2/chain/SPY/expirations");
      expect(calls[3]?.url.searchParams.get("at")).toBe("2026-08-29");
      expect(calls[3]?.url.searchParams.get("expiry")).toBe("2026-09-18");
      expect(calls[3]?.url.searchParams.get("strike_gte")).toBe("500000");
      expect(calls[3]?.url.searchParams.has("date")).toBe(false);
      expect(schema.data).toContain("proto3");
      expect(new Headers(calls[4]?.init?.headers).get("accept")).toBe("text/plain");
    });
});

describe("conditional requests", () => {
  it("returns ETags and represents a 304 without treating it as an API error", async () => {
    let calls = 0;
    const client = new ITMClient({
      fetch: async (_input, init) => {
        calls += 1;
        if (calls === 1) {
          return Response.json(
            { data: { symbol: "SPY" }, meta: {} },
            { headers: { etag: '"symbol-v1"' } },
          );
        }
        expect(new Headers(init?.headers).get("if-none-match")).toBe('"symbol-v1"');
        return new Response(null, {
          status: 304,
          headers: { etag: '"symbol-v1"' },
        });
      },
    });

    const fresh = await client.request<JsonObject>("/v2/symbols/SPY");
    const unchanged = await client.request<JsonObject>("/v2/symbols/SPY", {
      ifNoneMatch: fresh.etag!,
    });

    expect(fresh.etag).toBe('"symbol-v1"');
    expect(unchanged).toMatchObject({
      status: 304,
      data: undefined,
      notModified: true,
      etag: '"symbol-v1"',
    });
  });
});
