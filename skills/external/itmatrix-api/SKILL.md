---
name: itmatrix-api
description: Build market-data workflows on the ITMatrixHQ API, including GEX analysis, calendar bars, options flow, off-exchange views, public streams and response metadata. Prefers the itmatrix Python or @itmatrixhq/core TypeScript SDK when installed and calls documented public operations over HTTPS otherwise. Not for publishing packages or operating the backend.
---

# ITMatrixHQ API

## Choose how to call it

- **SDK first when it is installed.** Python: `itmatrix` (`itmatrix.ITMClient` sync, `itmatrix.AsyncITMClient` async). TypeScript: `ITMClient` from `@itmatrixhq/core`. The SDKs handle auth, the envelope, bounded retries, protobuf decoding and typed models. Check the installed version's README or types for signatures; this skill may outlive a package release.
- **Direct HTTPS is fine too.** Use it when no SDK is installed, the language has none, or a named method does not cover what you need. Use `curl`, `httpx`, `fetch` or the SDK's `request("/v2/...")` escape hatch. The served `GET /v2/openapi.json` lists every operation with its parameters and schemas. `/v2/rest-protocol.proto` and `/v2/ws-protocol.proto` are the protobuf schemas.
- **Stay on the documented public surface.** Call only the `/v2` operations that the OpenAPI document marks `x-exposure: public`. Fetching the document itself and the two `.proto` files is always fine. Never call internal, undocumented or guessed routes, and skip the pre-`/v2` aliases.

Take credentials from the application's configured environment and keep them out of logs and generated output.

## Direct HTTPS rules

- Base URL: the one the application configures. The default is `https://api.itmatrixhq.com`, which both SDKs export as `DEFAULT_BASE_URL`.
- Auth: send `Authorization: Bearer itm_…` on every request.
- Media types: `Accept: application/json` by default. Send `application/x-protobuf` only on operations whose OpenAPI response lists it, and decode with the served `.proto`. Request no other media type.
- Envelope: success is `{data, meta}`. An error is `{error: {code, message, details}}`, and the `x-request-id` response header identifies the request.
- Retries: retry only GETs that return 429, 502, 503 or 504. Honour `Retry-After` and cap the attempts. Never retry a mutation blindly.
- Caching: send `If-None-Match` with a stored ETag. A 304 has no body, so reuse data only if you actually kept it.
- Streams: `POST /v2/stream/ticket`, then the WebSocket at `/v2/ws` framed by the ws protobuf schema. Prefer the SDK stream, which already handles tickets, reconnects and backpressure. Do not hand-build a keyed GEX stream. It needs the SDK protocol (see Streams).

## Choose the smallest useful method

- Direct reads: `get_gex` / `getGex`, `get_option_chain` / `getOptionChain`, `get_bars` / `getBars`.
- For a New York calendar period, use `get_bars_for_period(symbol, "today")` or `getBarsForPeriod(symbol, "today")`. The other periods are `week_to_date`, `month_to_date`, `year_to_date`, and `calendar_month` with a `YYYY-MM` month. Today defaults to 1-minute bars and longer periods to daily bars. These return **one page**, not a complete observed month.
- For GEX levels, `get_gex_analysis` / `getGexAnalysis` fetches and analyzes in one call, and `analyze_gex` / `analyzeGex` works on a grid you already have. Server `net_gex` and the nullable `flip_point` give net exposure and zero gamma. The positive and negative level rankings cover only the returned strike rows, so `top` or tier limits can hide stronger levels. Do not call them full-chain walls, and do not infer market direction from the sign of exposure.
- `flow` gives bounded public options-flow views. `offexchange` gives the synthetic activity, concentration, profile and composition views, and `levels`, off-exchange volume by price level per session (end of day; no buy or sell side). Live large trades need a symbol or a premium floor. EOD flow needs a session plus one of those. For an API key the three flow reads and the four synthetic off-exchange views need a non-professional classification on the account. Without it the key gets `403 attestation_required`. `levels` does not need it.

## Keep what the response says

- Keep `data` together with its metadata, HTTP status, request ID and ETag whenever the caller needs provenance or cache behaviour.
- Before presenting a result as current or complete, check freshness, coverage, session or capture, caps, and any `partial` or `delayed` signal.
- Keep nulls as nulls. An unavailable zero gamma or a missing price is not zero.
- Bars are split-adjusted unless `adj="none"`. For point-in-time fundamentals in a backtest or screen, use `vintage="as_reported"` with `as_of`; the default `latest` view includes later restatements.
- If the client warns that a newer SDK release exists, say so and give the upgrade command. In Python, `result.raw` holds fields newer than the installed models.
- Units:
  - GEX values are dollars of dealer hedging per $1 move. Each has a served `*_shares` sibling in shares of the underlying; use it rather than dividing by a price yourself.
  - Timestamps ending in `_ms` are epoch milliseconds.
  - Strikes and strike filters are integer thousandths of dollars where the schema says so.
- On entitlement errors, surface the code and the request ID.
- For lists or bars, follow only the cursor the response provides (`meta.cursor`). Bound the page and row counts, and stop on a missing or repeated cursor. Do not quietly turn a first page into a monthly aggregate or a full-history study.

## Streams

Python streams use `AsyncITMClient`. TypeScript streams use `client.stream`. Keep one shared connection and a bounded set of subscriptions, and close them through their lifecycle methods. A ticket authorizes one connection handshake: mint a new ticket for a real reconnect, not because a healthy connection's ticket expired. Treat a backpressure error as data loss that needs an explicit recovery decision.

### Live GEX book for API keys

SDK 1.2.0 streams GEX to `itm_` keys on API Explorer and API Pro. If key streaming is switched off for the account, the key gets `not_entitled_tier` with the reason `stream_preview`. Do not retry that error. Check that the installed SDK is 1.2.0 or later before using it.

- `spy = client.stream.gex("SPY", nearest=20)` (TypeScript `client.stream.gex("SPY", { nearest: 20 })`) returns a live book at once. Lead with handlers: `spy.on("update", fn)` gets each new book, `spy.on("status", fn)` gets `connecting`, `live`, `reconnecting` or `closed`, and `spy.on("error", fn)` gets errors. End the script with `await client.run()`, which returns when every live book has closed (Python closes them on Ctrl-C, TypeScript needs `process.on("SIGINT", () => client.close())`). Read `spy.latest` when you need the newest book on demand, and `await spy.changed()` to wait for the next. Do not poll `latest` in a loop.
- Handlers are conflated: a busy handler is next called with the newest book, never a backlog. A handler that raises goes to `error` and never stops the stream. On Python `AsyncITMClient` keep plain handlers quick or make them `async def`. On the blocking `ITMClient` handlers run on the book's own thread or an executor you pass.
- A book iterates rows with `strike`, `call`, `put` and `total`, and has `spot`, `net_gex` (`netGex`), `flip` (nullable) and `time`. Keep a null `flip` as null. The book holds the 120 strikes with the largest gross GEX and updates about every 5 seconds. Delivery is conflated: you get the newest book, never a backlog.
- The stream carries `gex` only. Spot, stocks, index, chain and market topics are refused for keys, and so are index symbols.
- One SDK client is one connection. Open one live book per symbol and keep it open. Limits are per account: Explorer 5 connections and 25 symbols, Pro 100 and 1,000, and 50 per connection. The server refuses over a limit and never evicts.
- Let the SDK reconnect. Do not add your own retry loop around it. `stream_preview`, `client_outdated`, `client_required`, `proto_required` and `protobuf_required` arrive as an `error` event and close the book. `connection_limit` needs at least 30 seconds of backoff.
- The stream requires the SDK protocol: protobuf, the `itmatrixhq.v1` subprotocol and a client name. The server cannot verify the SDK was used. Do not present this as a security control. Details: `docs/streaming.md`.

## Minimal examples

```python
import os
import itmatrix as itm

client = itm.ITMClient(api_key=os.environ["ITM_API_KEY"])  # close() is optional
result = client.get_gex_analysis("SPY", levels=3)
print(result.data.net_gex, result.data.zero_gamma, result.data.levels_scope)
month = client.get_bars_for_period("SPY", "calendar_month", month="2026-09")
print(month.meta.get("cursor"))  # continue if complete history is required
```

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY }); // REST needs no close()
const analysis = await client.getGexAnalysis("SPY", { levels: 3 });
console.log(analysis.data.netGex, analysis.data.zeroGamma);
```

```sh
curl -fsS -H "Authorization: Bearer $ITM_API_KEY" \
  "https://api.itmatrixhq.com/v2/gex/SPY/grid?top=10"
```
