import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ITMClient, SDK_VERSION } from "../src/index.js";
import { CLIENT_NAME, SILENCE_ENV, resetUpgradeNoticeForTests } from "../src/version.js";

const LEVEL = {
  ticker: "NVDA",
  date: "2026-11-05",
  price_thousandths: 191_250,
  volume: 1200,
  notional: 229_500,
  trade_count: 7,
};

function recording(headers: Record<string, string> = {}, status = 200) {
  const requests: Request[] = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(new Request(input, init));
    return status === 200
      ? Response.json({ data: [LEVEL], meta: {} }, { headers })
      : Response.json({ error: { code: "not_entitled_tier", message: "no" } }, { status, headers });
  };
  return { requests, fetch: fetch as typeof globalThis.fetch };
}

describe("version", () => {
  it("matches the package version", () => {
    const pkg = JSON.parse(readFileSync(resolve(process.cwd(), "package.json"), "utf8")) as { version: string };
    expect(SDK_VERSION).toBe(pkg.version);
    expect(CLIENT_NAME).toBe(`itmatrix-typescript/${pkg.version}`);
  });
});

describe("dark-pool levels", () => {
  it("reads the keyed route with only its params, as JSON on either transport", async () => {
    const { requests, fetch } = recording();
    const client = new ITMClient({ fetch, transport: "protobuf" });
    const result = await client.offexchange.levels("nvda", {
      from: "2026-11-01", to: "2026-11-05", tz: "UTC", limit: 50, cursor: "c1",
    });
    const url = new URL(requests[0]!.url);
    expect(url.pathname).toBe("/v2/darkpool/NVDA/levels");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      from: "2026-11-01", to: "2026-11-05", tz: "UTC", limit: "50", cursor: "c1",
    });
    expect(requests[0]!.headers.get("accept")).toBe("application/json");
    expect(result.data).toEqual([LEVEL]);
  });

  it("is reachable through the darkpool alias with no range", async () => {
    const { requests, fetch } = recording();
    await new ITMClient({ fetch }).darkpool.levels("SPY");
    expect(new URL(requests[0]!.url).search).toBe("");
  });
});

describe("new parameters", () => {
  it("sends bars adj on the typed, wire and period reads", async () => {
    const { requests, fetch } = recording();
    const client = new ITMClient({ fetch });
    await client.bars("aapl", { from: "2026-11-03", to: "2026-11-04", adj: "none" });
    await client.getBarsForPeriod("AAPL", "today", { adj: "split" });
    await client.bars("AAPL", { from: "2026-11-03", to: "2026-11-04" });
    expect(new URL(requests[0]!.url).searchParams.get("adj")).toBe("none");
    expect(new URL(requests[1]!.url).searchParams.get("adj")).toBe("split");
    expect(new URL(requests[2]!.url).searchParams.has("adj")).toBe(false);
  });

  it("sends the as-reported fundamentals vintage", async () => {
    const { requests, fetch } = recording();
    await new ITMClient({ fetch }).fundamentals.get("msft", "income", {
      vintage: "as_reported", asOf: "2025-06-30",
    });
    const url = new URL(requests[0]!.url);
    expect(url.pathname).toBe("/v2/fundamentals/MSFT/income");
    expect(url.searchParams.get("vintage")).toBe("as_reported");
    expect(url.searchParams.get("as_of")).toBe("2025-06-30");
  });

  it("refuses asOf without the as-reported vintage before any request", () => {
    const { requests, fetch } = recording();
    const client = new ITMClient({ fetch });
    expect(() => client.fundamentals.get("MSFT", "income", { asOf: "2025-06-30" })).toThrow(/as_reported/);
    expect(() => client.fundamentals.get("MSFT", "income", { vintage: "latest", asOf: "2025-06-30" }))
      .toThrow(/as_reported/);
    expect(requests).toHaveLength(0);
  });
});

describe("sdk header and upgrade notice", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    resetUpgradeNoticeForTests();
    delete process.env[SILENCE_ENV];
    warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    warn.mockRestore();
    delete process.env[SILENCE_ENV];
    resetUpgradeNoticeForTests();
  });

  it("declares the sdk on server runtimes and can be turned off", async () => {
    const on = recording();
    await new ITMClient({ fetch: on.fetch }).request("/v2/symbols");
    expect(on.requests[0]!.headers.get("x-itm-sdk")).toBe(CLIENT_NAME);
    const off = recording();
    await new ITMClient({ fetch: off.fetch, sdkHeader: false }).request("/v2/symbols");
    expect(off.requests[0]!.headers.has("x-itm-sdk")).toBe(false);
  });

  it("warns once per module when a newer minor is advertised", async () => {
    const { fetch } = recording({ "X-ITM-SDK-Current": "9.4.0" });
    const client = new ITMClient({ fetch });
    await client.request("/v2/symbols");
    await client.request("/v2/symbols");
    await new ITMClient({ fetch }).request("/v2/symbols");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]![0])).toContain("npm install @itmatrixhq/core@latest");
  });

  it("stays quiet for the same minor, older or garbled versions", async () => {
    const [major, minor] = SDK_VERSION.split(".");
    for (const advertised of [`${major}.${minor}.0`, `${major}.${minor}.9`, "0.9.1", "latest", ""]) {
      const { fetch } = recording({ "X-ITM-SDK-Current": advertised });
      await new ITMClient({ fetch }).request("/v2/symbols");
    }
    expect(warn).not.toHaveBeenCalled();
  });

  it("is silenced by the option or the environment", async () => {
    const { fetch } = recording({ "X-ITM-SDK-Current": "9.4.0" });
    await new ITMClient({ fetch, upgradeNotice: false }).request("/v2/symbols");
    process.env[SILENCE_ENV] = "1";
    await new ITMClient({ fetch }).request("/v2/symbols");
    expect(warn).not.toHaveBeenCalled();
  });

  it("also rides error responses", async () => {
    const { fetch } = recording({ "X-ITM-SDK-Current": "9.4.0" }, 403);
    await expect(new ITMClient({ fetch }).request("/v2/symbols")).rejects.toThrow();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
