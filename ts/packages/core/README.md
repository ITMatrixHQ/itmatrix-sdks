# ITMatrixHQ TypeScript SDK

```sh
npm install @itmatrixhq/core
```

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const { data: grid, meta } = await client.getGex("SPY", { top: 10 });
console.log(grid.net_gex, grid.strikes[0]?.gex, meta.cursor);
const { data: identity } = await client.lookupSymbol("SPY");
```

The client needs no disposal for REST calls: fetch holds nothing between
requests, so an unused client is simply garbage. Call `client.close()` after using
`client.stream`, to end its subscriptions, socket and reconnect timer; it is
idempotent, and later calls on that client reject with a clear error.

Node 22+ and modern browsers are supported. Keep API keys on your server.

`getGex`, `getOptionChain` and `getBars` return typed domain models
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

Server entitlements still apply. Bars and option chains are public to API keys. Flow and off-exchange reads
also need a non-professional classification on the account. Without it a key gets
`403 attestation_required`. Index symbols are `403 not_entitled_symbol` for keys.
`listSymbols`, `getSymbol`, and `lookupSymbol` expose registry and identity models.
Grouped resources cover account, offexchange, economy, flow, fundamentals,
journal, market, reference, screener, vol, watchlists, protocol schemas and
service info. Together with the client's own methods they model **every public
`/v2` operation the contract declares** — the pre-`/v2` aliases are
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

// Our own implied vol, out of the greeks engine.
const term = await client.vol.term("SPY");

// Off-exchange volume by price level over five sessions, first page.
const levels = await client.offexchange.levels("NVDA", { from: "2026-11-03", to: "2026-11-07" });

// Prices as traded rather than split-adjusted.
const asTraded = await client.getBars("NVDA", { from: "2024-06-03", to: "2024-06-14", timeframe: "1d", adj: "none" });

// The income statement as it had been reported on a past date.
const income = await client.fundamentals.get("MSFT", "income", { vintage: "as_reported", asOf: "2025-06-30" });

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

On Node, Deno and Bun every request names the SDK in
`X-ITM-SDK: itmatrix-typescript/<version>`. Browsers send it only with
`sdkHeader: true`, because a custom header makes every cross-origin call
preflight. When the API answers `X-ITM-SDK-Current` with a newer minor or major
release, the client warns once through `console.warn` with the upgrade command.
It never throws and patch releases never trigger it. Silence it with
`upgradeNotice: false` or `ITMATRIX_NO_UPGRADE_CHECK=1`. JSON results carry every
field the API sends, including ones newer than your installed types.

Requests time out after 30 seconds. `timeoutMs` changes that budget per attempt;
GET retries default to two for 429/502/503/504, with each delay capped at 30 seconds.
Writes are never automatically retried. `ITMError` includes the HTTP status,
server code, message, request ID, and retry hint.

`gex`, `chain` and `bars` are legacy wire-level methods: with protobuf
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

### Live GEX book (API key)

A live book is an object that keeps the newest GEX book for one symbol and calls
your handlers when it changes. You do not run a loop.

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const spy = client.stream.gex("SPY", { nearest: 20 });
spy.on("update", (book) => {
  console.log(book.symbol, book.spot, book.netGex, book.flip);
  for (const row of book) console.log(row.strike, row.call, row.put, row.total);
});
spy.on("status", (status) => console.log("status:", status));
spy.on("error", (error) => console.error("error:", error));
process.on("SIGINT", () => client.close());
await client.run();                        // resolves when every book has closed
```

`client.stream.gex(symbol)` needs an `itm_` API key on API Explorer or API Pro and
SDK 1.2.0 or later (`npm i @itmatrixhq/core@latest`). It returns at once and connects
in the background. Every live book on a client shares one connection.

| Event | Fires when | Handler receives |
|---|---|---|
| `update` | A new book is ready, and right after `update(...)` changes the filter | a `Book` |
| `status` | The connection state changes | `"connecting"`, `"live"`, `"reconnecting"` or `"closed"` |
| `error` | A handler threw or rejected, or the stream hit a problem | the error (`StreamError` for stream refusals) |

A plain handler runs inline, so keep it quick. An async handler is awaited one call
at a time. Delivery is conflated: a busy handler is next called with the newest book,
never a backlog. A handler that throws goes to `error` and never stops the stream.

Besides handlers: `spy.latest` is the newest book, `spy.age` its age in seconds,
`await spy.ready()` the first book, `await spy.changed()` the next one and
`for await (const book of spy)` iterates conflated books. `spy.update({ within: 0.05 })`
changes the filter and `spy.close()` ends the book (`await using` works where the
runtime supports it). `client.run()` resolves when every live book has closed. It
installs no signal handler, so close the client on `SIGINT` as shown.

A book has `symbol`, `spot`, `netGex` (the whole-chain total), `flip` (or `null`),
`time` (a `Date`), `age` and rows with `strike`, `call`, `put` and `total`. `get(600)`
finds one strike, `rows` is the array and `columns` gives typed arrays. `meta` holds
`bookId` (a `bigint`), `version`, `changed` (the strikes that moved) and `reason`. The
book holds the 120 strikes with the largest gross GEX and carries no open interest.

Filters are an options object: `nearest`, `within` (a fraction of spot, `0.02` is 2%)
or `between: [lo, hi]`, optionally with `top` and `by` (`"gross"`, `"net"`, `"call"`
or `"put"`). They choose among the book's 120 strikes, so `nearest: 20` can have gaps.

The SDK keeps the book correct. It checks a CRC on every frame, asks for a fresh
keyframe on any gap, backs off with jitter and resubscribes after a reconnect. A
refusal it cannot retry is an `error` with `terminal` set, then the book closes.
Recipes (flip alerts, many symbols, web handlers, reconnects, async I/O) are in the
[streaming guide](../../../docs/streaming.md#recipes). See also `examples/gex-stream.ts`.

From `ts/`, run `npm ci`, `npm run typecheck`, `npm test`, and
`npm run build --workspace @itmatrixhq/core`. Contributions should add a focused
regression test for a behavior change; regenerate wire files only for an actual
public protocol change.
