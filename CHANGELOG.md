# Changelog

Changes to the ITMatrixHQ Python and TypeScript SDKs. The two versions move together.

## 1.1.0 — 2026-11-14

Pinned to API contract 2.20.0. Streaming GEX with an API key needs this release. The API refuses 1.0.0.

Added
- GEX book stream for API keys, in both languages. API Explorer and API Pro keys can stream it. `stream.gex_book(symbol)` in Python and `stream.gexBook(symbol)` in TypeScript yield a `GexBookState` after every keyframe or update. It needs an `itm_` API key. Any other credential raises a `StreamError` with the reason `api_key_required`.
- Each strike carries call GEX and put GEX: `call_gex` and `put_gex` (`callGex`, `putGex`). `gex` is their sum, computed in the SDK. The book holds the 120 strikes with the largest gross GEX, `|call| + |put|`, with spot, net GEX and the flip point. The book updates about every 5 seconds.
- The client keeps the book correct. It checks a CRC over the strikes and both sides on every keyframe and update, ignores duplicates, and asks for a fresh keyframe on a gap or a mismatch. It reconnects, resubscribes and backs off as the service asks. Applying an update rewrites only the strikes that changed.
- Filters: `nearest(n)`, `within_pct(p)` (`withinPct`), `between(lo, hi)`, `top(n, by)` and `all()`. Pass one as `filter=` to `gex_book` or `{ filter }` to `gexBook`, and swap it on a live subscription with `set_filter` (`setFilter`). Filters choose among the book's 120 strikes, so `nearest(20)` can have gaps.
- `StreamError` carries the server's `reason`, whether the failure is `terminal` and the `Retry-After` wait. Python exports `GexBookState`, `GexBookSubscription` and `StreamError`. TypeScript exports them from `@itmatrixhq/core`.
- Keys connect with protobuf protocol 8, the `itmatrixhq.v1` subprotocol and a client name and version: `itmatrix-python/<version>` or `itmatrix-typescript/<version>`. The book messages are `GexStrikeBook` and `GexStrikeBookUpdate` (`Frame` fields 23 and 24).
- Off-exchange volume by price level: `offexchange.levels(symbol, ...)` in both languages (`darkpool.levels` is the same method). It reads `GET /v2/darkpool/{symbol}/levels`, the one dark-pool dataset an API key can read, with `from`, `to`, `tz`, `limit` and `cursor`. Without a range it returns the latest session. JSON on either transport.
- `adj` on bars: `"split"` (the default, every bar rescaled to today's share count) or `"none"` (as traded). On `get_bars` / `getBars`, the period helpers and the wire-level `bars`.
- `vintage` and `as_of` / `asOf` on `fundamentals.get` for `income` and `balance-sheet`. `vintage="as_reported"` returns what had been observed at `as_of`, which is the view a backtest or screen should use. `as_of` without `vintage="as_reported"` is refused before any request.
- Python `Result.raw`: the response body as received, before typing. A field the API adds after this release can be read there before the SDK types it. TypeScript JSON results already carry every field the API sends.
- Every request names the SDK in `X-ITM-SDK` (`itmatrix-python/<version>`, `itmatrix-typescript/<version>`). TypeScript sends it on Node, Deno and Bun, and in browsers only with `sdkHeader: true`, because a custom header makes every cross-origin call preflight.
- Upgrade notice. When the API answers `X-ITM-SDK-Current` with a newer minor or major release, the client warns once per process with the upgrade command: `ITMUpgradeWarning` in Python, `console.warn` in TypeScript. Patch releases never warn, and the notice never raises. Silence it with `ITMATRIX_NO_UPGRADE_CHECK=1`, a `warnings` filter in Python, or `upgradeNotice: false` in TypeScript.
- Examples: `python/examples/gex_stream.py` and `ts/examples/gex-stream.ts`.
- Documentation: new `docs/streaming.md`, protocol versions in `docs/transport.md`, README and agent skill sections.

Changed for every stream connection, including signed-in sessions
- Reconnect backoff uses full jitter, so many clients no longer retry in step.
- The client waits 20 seconds for authentication, then gives up and retries.
- The client sends its own ping every 20 seconds and reconnects after 40 seconds of silence.
- A reconnect that meets a client error from the ticket request, such as a revoked key, stops and raises. It no longer retries forever. Timeouts and `429` answers are still retried. `Retry-After` is honored up to 300 seconds, and `connection_limit` waits at least 30 seconds.

Unchanged
- Signed-in sessions keep protocol 3. `stream.gex()` is unchanged.
- Existing REST methods and models are unchanged. The additions above are new optional parameters and one new method.

## 1.0.0 — 2026-09-27

First public release, pinned to API contract 0.9.0. The default base URL is `https://api.itmatrixhq.com`.
