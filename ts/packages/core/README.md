# ITMatrixHQ TypeScript SDK

```sh
npm install @itmatrixhq/core
```

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const { data: grid, meta } = await client.getGex("SPY", { top: 10 });
console.log(grid.net_gex, grid.strikes[0]?.gex, meta.plane);
const { data: identity } = await client.lookupSymbol("SPY");
```

The client needs no disposal for REST calls: fetch holds nothing between
requests, so an unused client is simply garbage. Call `client.close()` after using
`client.stream`, to end its subscriptions, socket and reconnect timer; it is
idempotent, and later calls on that client reject with a clear error.

Node 22+ and modern browsers are supported. Keep API keys on your server. Browser
applications can supply `token: async () => freshToken` for their authenticated session.

`getGex`, `getOptionChain`, `getBars`, and `getReplay` return typed domain models
with the same shape under JSON (default) and `transport: "protobuf"`. Prices are
dollars, timestamps are epoch milliseconds, dates are ISO calendar dates, and
strikes/strike filters are **integer thousandths** (`600_000` means $600).
GEX values (`gex`, `net_gex`, `max_abs_gex`, `delta_adj`, `gex_0dte`) are dollars
of dealer hedging per $1 move. Each has a served `*_shares` sibling (`gex_shares`,
`net_gex_shares`, `max_abs_gex_shares`, `delta_adj_shares`, `gex_0dte_shares`) in
shares of the underlying per $1 move, on the grid, history and reference book;
`null` means the server does not know it.
Missing vendor values stay missing; missing capture information stays `null`.
Responses retain `meta`, `status`, `etag`, and `requestId`. Bar cursors are in
`meta.cursor`; pass the cursor to the next `getBars` call.

```ts
const client = new ITMClient({ apiKey: process.env.ITM_API_KEY, transport: "protobuf" });
const expiries = await client.listExpirations("SPY");
const chain = await client.getOptionChain("SPY", {
  expiry: expiries.data[0], strikeGte: 590_000, strikeLte: 610_000,
});
for (const row of chain.data.rows) {
  console.log(row.contract.expiry, row.contract.right, row.contract.strike / 1000, row.gamma);
}
```

Server entitlements still apply. Bars, replay, options chains, and several grouped
resources are app-only; supplying an API key does not make those routes public.
`listSymbols`, `getSymbol`, and `lookupSymbol` expose registry and identity models.
Grouped resources cover account, offexchange, economy, flow, fundamentals/news,
journal, market, reference, screener, vol, watchlists, protocol discovery and
service info. Together with the client's own methods they model **every
non-internal `/v2` operation the contract declares** — the pre-`/v2` aliases are
deliberately not modelled.

`client.offexchange` exposes `activity`, `concentration`, `profile`, and
`composition`. `client.darkpool` aliases the same safe resource. No generic
dataset or raw-evidence method exists; protobuf transport is automatic when selected.
Less stable endpoint payloads use `JsonValue`; `request<T>("/v2/…", options)` is
the explicit escape hatch.

```ts
// A whole session's GEX captures. `to` is exclusive, so 16:00 drops the close.
const day = await client.getGexHistory("SPY", { date: "2026-09-04", from: "09:25", to: "16:05", top: 120 });

// Exact immutable book; captured_at=null means unavailable, not zero.
const reference = await client.getGexReference("SPY", {
  date: "2026-09-25", basis: "prev_close",
});

// First-party app session only: Pro + CBOE attestation, explicitly partial.
const premium = await client.flow.premium("SPY", {
  sessions: 2, dte: "zero", minPremiumUsd: 100_000,
});

// Our own implied vol, out of the greeks engine.
const term = await client.vol.term("SPY");

// Is the API up, and what does this deployment serve? No credential needed.
const { data } = await client.info.ready();
const spec = await client.info.openapi();
```

Local convenience methods add no API route:

```ts
const { data: analysis } = await client.getGexAnalysis("SPY");
console.log(analysis.netGex, analysis.zeroGamma, analysis.positiveLevels[0]);
const { data: monthDays } = await client.getBarsForPeriod(
  "SPY", "calendar_month", { month: "2024-02" },
);
```

`zeroGamma` is the server's nullable flip point; positive and negative levels
are ranked among the returned rows, which may be tier-limited. Periods are New
York calendar dates: `today` defaults to 1-minute bars, while week/month/year
to date and explicit calendar months default to daily bars. A timeframe that
would silently hit the backend's range cap is rejected locally. The helper
returns one page; inspect `meta.cursor` before treating a range as complete. See
[convenience semantics](../../../docs/convenience.md).

`info.health()` and `info.ready()` are the only calls that leave `/v2/`: the
client keeps a two-entry allowlist for them and rejects every other path, so
`request()` cannot wander off the documented surface. `ready()` is the deploy
contract rather than a synonym for `health()` — it is non-2xx while the process
is still warming up.

`baseUrl` defaults to the exported `DEFAULT_BASE_URL`, the one place the package
defines it.

Requests time out after 30 seconds. `timeoutMs` changes that budget per attempt;
GET retries default to two for 429/502/503/504, with each delay capped at 30 seconds.
Writes are never automatically retried. `ITMError` includes the HTTP status,
server code, message, request ID, and retry hint.

`gex`, `chain`, `bars`, and `replay` are legacy wire-level methods: with protobuf
they return generated wire objects, including bigint fields. Prefer the `get…`
methods for domain models. Domain conversion rejects protobuf integers beyond
JavaScript's safe integer range instead of rounding; advanced users can access
`restWire` and legacy methods when exact bigint processing is required. JSON
numbers follow JavaScript's normal numeric precision limits.

`client.stream` keeps the public protobuf WebSocket transport with ticket renewal
and reconnect behavior. The REST `MarketDataCodec` interface lets an application
inject its own separately distributed codec through `new ITMClient({ codec })`.
Only normalized `get…` methods use this seam. The public distribution contains
JSON and public protobuf support; it contains no site-specific codec implementation.

From `ts/`, run `npm ci`, `npm run typecheck`, `npm test`, and
`npm run build --workspace @itmatrixhq/core`. Contributions should add a focused
regression test for a behavior change; regenerate wire files only for an actual
public protocol change.
