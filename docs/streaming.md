# Streaming GEX with an API key

The GEX stream is available to API Explorer and API Pro keys with SDK 1.1.0 or later. Install or upgrade with `pip install -U "itmatrix[stream]"` or `npm i @itmatrixhq/core@latest`. Earlier SDK versions cannot open the stream.

The stream delivers live gamma exposure (GEX) for one underlying per subscription. It is the same data as `get_gex`, pushed as it changes. An API key can subscribe to `gex` only. Other topics are refused for keys.

## Requirements

The stream requires the ITMatrixHQ SDK protocol. That means protobuf frames, the `itmatrixhq.v1` WebSocket subprotocol, protocol version 8 and a declared client name and version in the first frame. The server checks each of these and refuses a connection that omits one. Use the official Python or TypeScript SDK. It does all of this for you.

`gex_book` (Python) and `gexBook` (TypeScript) need an `itm_` API key. With any other credential the SDK raises a `StreamError` with the reason `api_key_required` and does not connect. Signed-in sessions keep protocol 3 and `stream.gex()`.

The server cannot prove that the SDK was used. The client name and subprotocol are declarations, and any program can send them. They exist so that connections identify themselves and so that old clients can be retired. They are not a security control. The reasons to use the SDK are that it keeps books correct, reconnects politely and stays within your limits.

## Quick start

Python:

```python
import os
import itmatrix as itm
from itmatrix import filters

async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
    async with client.stream.gex_book("SPY", filter=filters.nearest(20)) as book:
        async for state in book:
            print(state.symbol, state.version, state.spot, state.net_gex, len(state.strikes))
```

TypeScript:

```ts
import { ITMClient, nearest } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
for await (const state of client.stream.gexBook("SPY", { filter: nearest(20) })) {
  console.log(state.symbol, state.version, state.spot, state.netGex, state.strikes.length);
}
client.close();
```

The iterator yields after every applied keyframe or update. Without a filter it yields the whole book. In Python, leaving the `async with` block ends the subscription. In TypeScript, leave the loop or call `close()` on the book. `stream.gex()` is unchanged. It returns raw frames and is for token principals only.

Failures reach your code when you iterate, not when you create the book. Python's `async with client.stream.gex_book(...)` does not wait for the socket, so a refusal such as `stream_preview` is raised from the `async for` loop. The one error raised on entering is `api_key_required`. In TypeScript every failure rejects the loop. All of them are `StreamError`, an `ITMError`, with `reason`, `terminal` and `retry_after_seconds` (`retryAfterSeconds`). The exception is a consumer that falls more than 64 states behind. That raises a plain `ITMError` and ends the subscription.

## The book state

`state` is a read-only `GexBookState`.

| Field | Meaning |
|---|---|
| `symbol` | The underlying. |
| `captured_at` / `capturedAt` | Server capture time of the frame. A UTC `datetime` in Python and a `Date` in TypeScript. |
| `book_id`, `version` | Identity and version of the book. See reconstruction below. `book_id` is an `int` in Python and a `bigint` in TypeScript. |
| `spot` | Underlying price in dollars. |
| `net_gex` | Whole-chain total in dollars per $1 move. It is not the sum of the listed strikes. |
| `flip_point` | Zero-gamma level in dollars, or null when it cannot be computed. |
| `strikes` | Strikes in dollars, ascending. |
| `strikes_thousandths` | The same strikes as integer thousandths of a dollar. A tuple of `int` in Python and a `Float64Array` in TypeScript. |
| `call_gex` / `callGex` | Call GEX per strike in dollars per $1 move. Zero or positive. Single-precision values. A tuple in Python and a `Float32Array` in TypeScript. |
| `put_gex` / `putGex` | Put GEX per strike in dollars per $1 move. Zero or negative. Single-precision values. A tuple in Python and a `Float32Array` in TypeScript. |
| `gex` | Total GEX per strike: call plus put, added in double precision. The server does not send it. A tuple in Python and a `Float64Array` in TypeScript. |
| `changed` | Indices into `strikes` updated by the latest frame. A keyframe lists every index. |
| `reason` | Why a keyframe was sent (`subscribe`, `periodic`, `resync`, `rebuild` or `recovery`), or null for an update. |

The book holds the 120 strikes with the largest gross GEX, `|call| + |put|`. Ties go to the lower strike. Gross is used so that a strike where a large call wall and a large put wall cancel out still makes the list. Membership is fixed between keyframes. The signs follow REST: calls positive, puts negative. The book carries no open interest. Use `get_gex` for that. In TypeScript the field names are camelCase, `strikes` is a `Float64Array`, `changed` is a `Uint32Array` and the state is frozen. In Python the state is a frozen dataclass. In both, every state owns its arrays, so you can keep one without it changing under you.

## Filters

A filter picks strikes from the book and keeps the header (symbol, time, book id, version, spot, net GEX, flip point, reason). Filters are pure functions. You can pass one to the subscription or call it on any state you hold.

| Python | TypeScript | Keeps |
|---|---|---|
| `nearest(n)` | `nearest(n)` | The `n` strikes closest to spot. Ties go to the lower strike. |
| `within_pct(p)` | `withinPct(p)` | Strikes with `\|strike / spot - 1\| <= p`. `0.02` means 2 percent. |
| `between(lo, hi)` | `between(lo, hi)` | Strikes from `lo` to `hi` dollars, both included. |
| `top(n, by="gross")` | `top(n, by)` | The `n` largest strikes by `gross`, `net`, `call` or `put`. `net` is `\|call + put\|`. `call` and `put` rank by size. The view stays in strike order. |
| `all()` | `all()` | Every strike. This is the default. |

Python imports them from `itmatrix.filters` and passes `filter=` to `gex_book`. TypeScript imports them from `@itmatrixhq/core`, by name or as the `filters` namespace, and passes `{ filter }` to `gexBook`. In the filtered view, `changed` lists positions in the view, not in the full book.

Filters choose among the book's 120 strikes, which are ranked by gross GEX. They never add a strike the server did not send. So `nearest(20)` can have gaps: a strike close to spot with little GEX may not be in the book. Check `strikes` rather than assuming even spacing.

You can swap the filter on a live subscription. Nothing is sent to the server and the subscription is not restarted. In Python, `set_filter(None)` goes back to the whole book. In TypeScript, `setFilter` also yields the current book through the new filter at once, with every strike in the view marked as changed.

Python:

```python
async with client.stream.gex_book("SPY") as book:
    book.set_filter(filters.within_pct(0.02))
```

TypeScript:

```ts
const book = client.stream.gexBook("SPY");
book.setFilter(withinPct(0.02));
```

The subscription's `state` holds the latest full book before the filter. In Python, `view` holds the latest filtered view.

## How the stream works

1. **Ticket.** The SDK calls `POST /v2/stream/ticket` with your key. A ticket expires 60 seconds after minting and authorizes one connection. The SDK mints a fresh ticket for every connect.
2. **Connect.** It opens `wss://api.itmatrixhq.com/v2/ws` with the subprotocol `itmatrixhq.v1`.
3. **Authenticate.** The first frame is JSON: `{"op":"auth","ticket":"...","proto":8,"encoding":"protobuf","client":"itmatrix-python/1.1.0"}`. The server answers `authed` or a refusal. The SDK waits 20 seconds for the answer and treats a longer wait as a failed connect. The client string is `itmatrix-python/<version>` for Python and `itmatrix-typescript/<version>` for TypeScript. SDK 1.1.0 sends `itmatrix-python/1.1.0` or `itmatrix-typescript/1.1.0`.
4. **Subscribe.** The SDK sends a `sub` control frame for topic `gex` with a symbol. Only `symbol` is accepted as a parameter. Control frames are JSON text. Data frames are binary protobuf.
5. **Keyframe, then updates.** The first frame for a subscription is a `GexStrikeBook` keyframe. It replaces all state for the symbol. Later frames are `GexStrikeBookUpdate` messages that carry only the strikes whose call or put GEX changed, plus spot, net GEX and flip point. Both sides are sent for every listed strike. A strike is listed when either side moved by more than 0.001 times the largest single side in the book.

The engine publishes about every 5 seconds today. We will tune the cadence and work on bringing it up. A keyframe is sent on subscribe, every 300 seconds, on resync, when the book is rebuilt and when the server has skipped updates for you. If more than 60 percent of the strikes changed, the server sends a keyframe instead of an update.

Explorer and Pro receive the same cadence. The rungs differ by limits only.

### Reconstruction rules

The SDK applies these rules. They are listed so you can reason about what you see.

- A `GexStrikeBook` replaces state, once its own CRC checks out. It adopts the keyframe's `book_id` and `version`, whatever they were before. A keyframe that fails its CRC or is malformed is not adopted. The SDK asks for another.
- An update applies only if its `book_id` matches and its `version` is exactly one more than the current version. Its CRC must match the book after applying.
- A `version` at or below the current one is a duplicate and is ignored.
- Any other gap, a different `book_id` or a CRC mismatch makes the SDK send `{"op":"resync","id":"..."}`. It drops updates until the next keyframe arrives. The SDK sends at most one resync per subscription every 5.5 seconds, because the server honors one every 5 seconds.
- Keyframes and updates are checked the same way. The CRC is CRC-32 (IEEE, as in zlib). It covers, for each strike in ascending order, the strike in thousandths as a little-endian signed 64-bit integer, then the single-precision call GEX bits, then the single-precision put GEX bits, both little-endian. The derived total is not covered.
- A new `book_id` means discard everything. It follows a session roll, an engine restart or a member strike leaving the grid. An open interest change is an ordinary update.

There is no resume across reconnects. A reconnect costs one keyframe per subscription.

## Reconnect and backoff

One SDK client owns one connection and the subscriptions on it. After a reconnect it resubscribes everything and each subscription receives a fresh keyframe.

| Situation | SDK behavior |
|---|---|
| Network error, timeout, `5xx` or unexpected close | Reconnect with exponential backoff from 0.25 s to 10 s. Each wait is a random time from zero up to the current limit (full jitter). |
| `Retry-After` on a `429` or other mint answer | Wait that long, capped at 300 seconds, then retry. |
| Refused with `connection_limit` | Wait 30 seconds plus 0 to 50 percent random jitter, so 30 to 45 seconds. This applies even when `Retry-After` is shorter. |
| `bye` with `draining` | Wait for `reconnect_after_ms`, then reconnect. The server is restarting. |
| `bye` with `slow_consumer` | Close code 1013. Wait for `reconnect_after_ms`, then reconnect. |
| `bye` with `reauth` | Reconnect immediately. Sessions are re-authenticated at least every 15 minutes, so a revoked key or a changed plan takes effect within that time. |
| Any other `4xx` at the ticket mint, such as a revoked key | Terminal. The SDK raises and does not retry. `408` and `429` are retried. |
| Refused with `stream_preview`, `client_outdated`, `client_required`, `protobuf_required` or `proto_required` | Terminal. The SDK raises and does not retry. |
| `not_entitled_tier` or `invalid_params` at authentication | Terminal, whatever the reason. |
| A `nack` on one subscription, such as `subscription_limit` | That subscription raises and ends. Other subscriptions on the connection continue. |

When the SDK gives up, every subscription on the client raises the same `StreamError` with `terminal` set. The one exception is Python without the `stream` extra, which raises `ImportError` with the install command.

### Heartbeat

The server pings every 15 seconds and closes a connection that is silent for 45 seconds. The SDK answers each ping with `pong`. It also sends its own `{"op":"ping"}` every 20 seconds and treats 40 seconds without any message from the server, of any kind, as a dead connection. It then closes the socket and reconnects. The SDK does not use the WebSocket library's own ping.

### Close and refusal reasons

A refusal at the WebSocket arrives as `{"op":"nack","code":"...","reason":"..."}`, followed by close code 1008. The `reason` is a top-level field of the nack. `limit` is also top-level when the refusal is about a limit. The SDK exposes both on the `StreamError`, with the limit under `details`.

| Code | Reason | Meaning and what to do |
|---|---|---|
| `not_entitled_tier` | `stream_preview` | Key streaming is switched off for the account. Do not retry. Contact ITMatrixHQ. |
| `invalid_params` | `protobuf_required` | The auth frame did not request protobuf. Use the SDK. |
| `invalid_params` | `proto_required` | The protocol version was not 8. Upgrade the SDK to 1.1.0 or later. |
| `invalid_params` | `client_required` | The subprotocol or client name was missing. Use the SDK. |
| `invalid_params` | `client_outdated` | The SDK is older than the minimum version. Upgrade. |
| `quota_exceeded` | `connection_limit` | The account is at its connection limit. Close a connection or wait. The `limit` field gives the number. |
| `quota_exceeded` | `subscription_limit` | The account or connection is at its subscription limit. Unsubscribe something first. |
| `invalid_params` | `unsupported_param` | A subscription carried a parameter other than `symbol`. |
| `not_entitled_tier` | none | A topic other than `gex` was requested. Keys receive `gex` only. |
| `not_entitled_symbol` | none | Index symbols, SPX GEX included, are not available. |

One reason comes from the SDK itself and never from the server: `invalid_params` with the reason `api_key_required`, raised when `gex_book` is used without an `itm_` key.

The ticket mint applies the same gate and the connection limit before any socket opens, and it answers over HTTP in the usual error envelope. An account with key streaming switched off gets `403 not_entitled_tier`. An account at its connection limit is `429 quota_exceeded` with a `Retry-After: 30` header. In both, the reason is `error.details.reason`, and the connection limit is in `error.details.limit`. Other reasons are in the top-level `reason` of the nack described above.

## Limits

Limits are counted per account across all of its keys. A request over a limit is refused. The server never closes an existing connection or subscription to make room.

| | Explorer | Pro |
|---|---|---|
| Concurrent connections | 5 | 100 |
| Active GEX subscriptions | 25 | 1,000 |
| Subscriptions per connection | 50 | 50 |

Ticket mints count against the normal request rate limits.

## Egress

Subscribe once per symbol and keep the subscription open. Do not resubscribe to get a fresh snapshot, because each subscribe costs a keyframe. Use one SDK client, which is one connection, and share it across your code. Open a second client only when you need more than 50 subscriptions. Reconnect storms are the main avoidable cost, so do not wrap the SDK in your own retry loop. Its backoff already follows the rules above.

As design estimates, a keyframe is about 1.6 KB and an update with 20 changed strikes is about 0.25 KB. Each changed strike costs 8 bytes of values, because both sides are sent. Measured figures will replace these once the stream has run in production. The stream does not use permessage-deflate.

## Reference

- Protobuf schema: `spec/proto/itmatrixhq/ws/v1/wire.proto`, also served at `/v2/ws-protocol.proto`. The book is `GexStrikeBook` and `GexStrikeBookUpdate`, `Frame` fields 23 and 24. `GexBook` and `GexBookUpdate` (fields 21 and 22) are an earlier draft. The server never sends them and the SDK does not decode them.
- Encodings: [transport.md](transport.md).
