import { describe, expect, it } from "vitest";
import { ITMClient, ITMError } from "../src/index.js";

/** A client whose fetch records every call and answers with `reply`. */
function probe(reply: (url: URL, init?: RequestInit) => Response | Promise<Response>) {
  const calls: Array<{ url: URL; init?: RequestInit }> = [];
  const client = new ITMClient({
    apiKey: "itm_test",
    fetch: async (input, init) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      return reply(url, init);
    },
  });
  return { client, calls };
}

const envelope = (data: unknown, meta: Record<string, unknown> = {}) =>
  Response.json({ data, meta });

describe("per-call cancellation", () => {
  it("composes the caller's signal with the client timeout", async () => {
    const controller = new AbortController();
    const { client, calls } = probe(() => envelope([]));
    await client.getBars("SPY", { from: 0, to: 1, signal: controller.signal });
    const signal = calls[0]?.init?.signal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal!.aborted).toBe(false);
    controller.abort(new Error("caller changed its mind"));
    // The composed signal follows the caller's, which is the whole point:
    // the client timeout can only shorten the deadline, never lengthen it.
    expect(signal!.aborted).toBe(true);
  });

  it("aborts an in-flight request", async () => {
    const controller = new AbortController();
    const client = new ITMClient({
      apiKey: "itm_test",
      fetch: (_input, init) =>
        new Promise((_resolve, reject) => {
          const signal = init?.signal;
          if (signal?.aborted) return reject(signal.reason as Error);
          signal?.addEventListener("abort", () => reject(signal.reason as Error));
        }),
    });
    const pending = client.getGex("SPY", { signal: controller.signal });
    // Abort after a turn of the loop, so the request is genuinely in flight
    // rather than aborted before `fetch` was ever reached.
    await Promise.resolve();
    controller.abort(new Error("cancelled"));
    await expect(pending).rejects.toThrow("cancelled");
  });

  it("never puts the signal on the wire", async () => {
    const controller = new AbortController();
    const { client, calls } = probe(() => envelope([]));
    await client.offexchange.activity("SPY", { limit: 5, signal: controller.signal });
    expect(calls[0]!.url.searchParams.get("signal")).toBeNull();
    expect(calls[0]!.url.searchParams.get("limit")).toBe("5");
  });
});

describe("synthetic-only off-exchange surface", () => {
  it("does not expose a generic evidence route", () => {
    const { client } = probe(() => envelope([]));
    expect("dataset" in client.offexchange).toBe(false);
    expect("prints" in client.offexchange).toBe(false);
    expect(client.darkpool).toBe(client.offexchange);
  });

  it("provides named aggregate helpers", async () => {
    const { client, calls } = probe(() => envelope([]));
    await client.offexchange.concentration("spy", { limit: 20 });
    expect(calls[0]!.url.pathname).toBe("/v2/offexchange/SPY/concentration");
    expect(calls[0]!.url.searchParams.get("limit")).toBe("20");
  });
});

describe("gex dte", () => {
  it("sends the filter and surfaces the echo", async () => {
    const { client, calls } = probe(() =>
      envelope({ spot: 600, net_gex: 1, max_abs_gex: 1, strikes: [] }, { dte: 0 }));
    const result = await client.getGex("SPY", { dte: 0, top: 3 });
    expect(calls[0]!.url.searchParams.get("dte")).toBe("0");
    expect(result.meta.dte).toBe(0);
  });

  it("sends nothing when no filter was asked for", async () => {
    const { client, calls } = probe(() =>
      envelope({ spot: 600, net_gex: 1, max_abs_gex: 1, strikes: [] }));
    const result = await client.getGex("SPY");
    expect(calls[0]!.url.searchParams.has("dte")).toBe(false);
    expect(result.meta.dte).toBeUndefined();
  });
});

describe("fundamentals and news options", () => {
  it("sends the statement query", async () => {
    const { client, calls } = probe(() => envelope([]));
    await client.fundamentals.get("AAPL", "income", { timeframe: "annual", limit: 4 });
    expect(calls[0]!.url.pathname).toBe("/v2/fundamentals/AAPL/income");
    expect(calls[0]!.url.searchParams.get("timeframe")).toBe("annual");
    expect(calls[0]!.url.searchParams.get("limit")).toBe("4");
  });

  it("revalidates a cached fundamentals read", async () => {
    const { client, calls } = probe(() => new Response(null, { status: 304, headers: { etag: '"v1"' } }));
    const result = await client.fundamentals.get("AAPL", "ratios", { ifNoneMatch: '"v1"' });
    expect(new Headers(calls[0]!.init?.headers).get("if-none-match")).toBe('"v1"');
    expect(result.status).toBe(304);
    expect("notModified" in result && result.notModified).toBe(true);
  });

  it("revalidates news and the digest without touching the query", async () => {
    const { client, calls } = probe(() => new Response(null, { status: 304 }));
    await client.fundamentals.news({ symbols: "SPY" }, { ifNoneMatch: '"n1"' });
    await client.fundamentals.newsDigest({ kind: "brief" }, { ifNoneMatch: '"d1"' });
    expect(calls[0]!.url.searchParams.get("symbols")).toBe("SPY");
    expect(new Headers(calls[0]!.init?.headers).get("if-none-match")).toBe('"n1"');
    expect(calls[1]!.url.searchParams.get("kind")).toBe("brief");
    expect(new Headers(calls[1]!.init?.headers).get("if-none-match")).toBe('"d1"');
    expect(calls[1]!.url.searchParams.has("ifNoneMatch")).toBe(false);
  });

  it("sends no conditional header when none was asked for", async () => {
    const { client, calls } = probe(() => envelope([]));
    await client.fundamentals.news();
    expect(new Headers(calls[0]!.init?.headers).has("if-none-match")).toBe(false);
  });
});

describe("non-envelope 2xx bodies", () => {
  it("names the route instead of presenting the body as a typed result", async () => {
    const { client } = probe(() => Response.json({ status: "ok" }));
    await expect(client.getBars("SPY", { from: 0, to: 1 })).rejects.toSatisfy(
      (error: unknown) =>
        error instanceof ITMError &&
        error.message.includes("/v2/stocks/SPY/bars") &&
        error.message.includes("envelope"),
    );
  });

  it("rejects a 2xx that is not JSON at all on a typed route", async () => {
    const { client } = probe(() =>
      new Response("<html>signed out</html>", { headers: { "content-type": "text/html" } }));
    await expect(client.getOptionChain("SPY")).rejects.toBeInstanceOf(ITMError);
  });

  it("leaves request() permissive, because the info routes are not enveloped", async () => {
    const { client } = probe(() => Response.json({ status: "ok", providers: {} }));
    const health = await client.info.health();
    expect(health.data).toEqual({ status: "ok", providers: {} });
  });
});

describe("lazy stream", () => {
  it("builds the websocket client only when it is asked for, then caches it", () => {
    const client = new ITMClient({ apiKey: "itm_test", fetch: async () => envelope([]) });
    expect(client.stream).toBe(client.stream);
  });
});
