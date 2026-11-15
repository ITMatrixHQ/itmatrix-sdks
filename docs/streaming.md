# Streaming GEX with an API key

The GEX stream is available to API Explorer and API Pro keys with SDK 1.2.0 or later. Install or upgrade with `pip install -U "itmatrix[stream]"` or `npm i @itmatrixhq/core@latest`.

The stream delivers live gamma exposure (GEX) for each symbol you open. It is the same data as `get_gex`, pushed as it changes. An API key can stream `gex` only.

## Requirements

The stream requires the ITMatrixHQ SDK protocol. That means protobuf frames, the `itmatrixhq.v1` WebSocket subprotocol, protocol version 8 and a declared client name and version in the first frame. The server checks each of these and refuses a connection that omits one. Use the official Python or TypeScript SDK. It does all of this for you.

`stream.gex` needs an `itm_` API key. With any other credential the live book reports a `StreamError` with the reason `api_key_required` and does not connect. Signed-in sessions keep protocol 3 and `stream.subscribe("gex", ...)`.

The server cannot prove that the SDK was used. The client name and subprotocol are declarations, and any program can send them. They exist so that connections identify themselves and so that old clients can be retired. They are not a security control. The reasons to use the SDK are that it keeps books correct, reconnects politely and stays within your limits.

## Quick start

`client.stream.gex(symbol)` returns a live book at once. It connects in the background and keeps the newest book for you. You register handlers for what you care about, then let the client run.

Python:

```python
import asyncio, os
import itmatrix as itm

async def main() -> None:
    async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
        spy = client.stream.gex("SPY", nearest=20)
        spy.on("update", lambda book: print(book))
        spy.on("status", lambda status: print("status:", status))
        spy.on("error", lambda error: print("error:", error))
        await client.run()

asyncio.run(main())
```

TypeScript:

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const spy = client.stream.gex("SPY", { nearest: 20 });
spy.on("update", (book) => console.log(String(book)));
spy.on("status", (status) => console.log("status:", status));
spy.on("error", (error) => console.error("error:", error));
process.on("SIGINT", () => client.close());
await client.run();
```

`client.run()` returns when every live book on the client has closed. In Python, Ctrl-C closes every book and `run()` returns, so the script exits without a traceback. In TypeScript, `run()` installs no signal handler. Close the client on `SIGINT` as shown.

## Events

Register a handler with `on(event, handler)`. It returns a function that removes the handler. `off(event, handler)` does the same. A book can have several handlers for each event.

| Event | Fires when | The handler receives |
|---|---|---|
| `update` | A new book is ready: the first keyframe, every update, every later keyframe, and right after `update(...)` changes the filter. | The new `Book`. `book.meta.changed` lists the strikes that moved. |
| `status` | The connection state of this book changes. | `"connecting"`, `"live"`, `"reconnecting"` or `"closed"`. |
| `error` | A handler raised, or the stream hit a problem. A refusal the SDK cannot retry is followed by `"closed"`. | The exception. A stream refusal is a `StreamError` with `code`, `reason` and `terminal`. |

Where handlers run:

| Client | Plain function | Coroutine or async function |
|---|---|---|
| Python `AsyncITMClient` | Inline on the event loop. Keep it quick. | As a task, one call at a time per handler. |
| Python `ITMClient` (blocking) | On the book's own thread, `itmatrix-gex-handlers`, one call at a time. Or on an executor you pass: `on("update", fn, executor=pool)`. Never on the network thread. | Refused with `TypeError`. Use `AsyncITMClient`. |
| TypeScript | Inline on the event loop. Keep it quick. | Awaited, one call at a time per handler. |

**Conflation is the contract.** A handler that is still busy when new books arrive is next called with the newest one. The books in between are skipped. `latest`, `changed()` and iteration work the same way. Nothing ever builds a backlog, so a slow handler cannot make you fall behind the market. Every book is complete, so skipping one loses nothing.

**Handlers never stop the stream.** A handler that raises, or a promise that rejects, is reported to the `error` handlers and the stream goes on.

## Recipes

### Alert when spot crosses the flip

```python
sides: dict[str, bool] = {}

def flip_alert(book: itm.Book) -> None:
    if book.flip is None:
        return
    above = book.spot > book.flip
    if sides.get(book.symbol, above) != above:
        print(f"{book.symbol} crossed the flip at {book.flip:.2f}")
    sides[book.symbol] = above

client.stream.gex("SPY").on("update", flip_alert)
```

```ts
const sides = new Map<string, boolean>();
client.stream.gex("SPY").on("update", (book) => {
  if (book.flip === null) return;
  const above = book.spot > book.flip;
  if ((sides.get(book.symbol) ?? above) !== above) console.log(`${book.symbol} crossed the flip at ${book.flip}`);
  sides.set(book.symbol, above);
});
```

To alert when net GEX changes sign instead, compare `book.net_gex > 0` (`book.netGex > 0`) the same way.

### Several symbols on one client

```python
for symbol in ("SPY", "QQQ", "IWM", "NVDA"):
    client.stream.gex(symbol, nearest=20).on("update", flip_alert)
await client.run()
```

```ts
for (const symbol of ["SPY", "QQQ", "IWM", "NVDA"]) {
  client.stream.gex(symbol, { nearest: 20 }).on("update", show);
}
await client.run();
```

Every book on a client shares one connection. The client keeps each book open until you close it, so you do not need to hold on to the objects. Books for different symbols arrive independently. There is no ordering promise across symbols.

### Change the filter while it runs

```python
spy = client.stream.gex("SPY", nearest=20)
spy.on("update", show)
spy.update(within=0.05)   # handlers get the re-filtered book at once
spy.update()              # back to the whole book
```

```ts
const spy = client.stream.gex("SPY", { nearest: 20 });
spy.on("update", show);
spy.update({ within: 0.05 });
spy.update({});
```

Filters run in the SDK on the book it already holds. Changing one sends nothing to the server and costs nothing.

### Read the latest book from your own code

```python
spy = client.stream.gex("SPY")

@app.get("/gex/spy")                      # FastAPI, Starlette or similar
async def gex_spy() -> dict:
    book = spy.latest or await spy.ready(timeout=5)
    return {"spot": book.spot, "flip": book.flip, "age": book.age,
            "rows": [row._asdict() for row in book]}
```

```ts
const spy = client.stream.gex("SPY");
app.get("/gex/spy", async (_req, res) => {
  const book = spy.latest ?? (await spy.ready(5000));
  res.json({ spot: book.spot, flip: book.flip, age: book.age, rows: book.rows });
});
```

`latest` costs nothing to read. To wait for the next book instead of polling, use `await spy.changed()`. With the blocking `ITMClient`, `spy.ready(timeout)` and `spy.changed(timeout)` block the calling thread.

### pandas

```python
def biggest(book: itm.Book) -> None:
    df = book.df()                         # columns: strike, call, put, total
    print(df.reindex(df.total.abs().sort_values(ascending=False).index).head(5))

client.stream.gex("SPY").on("update", biggest)
```

`df()` needs pandas (`pip install pandas`). The columns `strikes`, `calls`, `puts` and `totals` are also on the book as plain sequences.

### Status and reconnects

```python
def on_status(status: str) -> None:
    if status == "reconnecting":
        log.warning("GEX stream reconnecting, latest book is %.0fs old", spy.age or 0)
    elif status == "live":
        log.info("GEX stream live")

spy.on("status", on_status)
spy.on("error", lambda error: log.error("GEX stream: %s", error))
```

```ts
spy.on("status", (status) => {
  if (status === "reconnecting") console.warn(`reconnecting, latest is ${spy.age ?? 0}s old`);
});
spy.on("error", (error) => console.error("GEX stream:", error));
```

The SDK reconnects on its own and each book gets a fresh keyframe after a reconnect. Do not add your own retry loop. A refusal it cannot retry, such as a revoked key, is an `error` with `terminal` set and then `closed`. When the last book closes, `client.run()` returns.

### A handler that does I/O

```python
async def post_net_gex(book: itm.Book) -> None:
    await http.post(WEBHOOK_URL, json={"symbol": book.symbol, "net_gex": book.net_gex})

spy.on("update", post_net_gex)             # http is an httpx.AsyncClient you own
```

```ts
spy.on("update", async (book) => {
  await fetch(WEBHOOK_URL, { method: "POST", body: JSON.stringify({ symbol: book.symbol, netGex: book.netGex }) });
});
```

An async handler is called one at a time. If the request is slow, newer books do not queue up behind it. The next call gets the newest book.

## The live book

Each symbol is its own object. Every object on one client shares one connection, and the connection closes when the last object closes.

| Python | TypeScript | What it does |
|---|---|---|
| `on(event, handler)` | `on(event, handler)` | Registers a handler. See [Events](#events). Returns a function that removes it. |
| `latest` | `latest` | The newest book, or none before the first. Read it any time. |
| `age` | `age` | Seconds since `latest` was computed. Use it to spot a stale book. |
| `status` | `status` | `connecting`, `live`, `reconnecting` or `closed`. |
| `await ready()` | `await ready()` | The first book. Returns `latest` at once if there is one. |
| `await changed()` | `await changed()` | The next book after now. Use it instead of polling `latest`. |
| `update(nearest=40)` | `update({ nearest: 40 })` | Replaces the filter. Handlers get the re-filtered book at once. |
| `close()` | `close()` | Ends the subscription. `async with` works in Python, and `await using` works in TypeScript where the runtime supports it. |
| `async for book in spy` | `for await (const book of spy)` | Iterates books as they arrive, conflated like handlers. |

On the client, `run()` waits until every live book has closed, and `close()` (`aclose()` in async Python) closes every live book on it.

## The book

A book is immutable. Iterate it for rows in strike order.

| Python | TypeScript | Meaning |
|---|---|---|
| `symbol` | `symbol` | The underlying. |
| `spot` | `spot` | Underlying price in dollars. |
| `net_gex` | `netGex` | Whole-chain total in dollars per $1 move. It is not the sum of the listed strikes. |
| `flip` | `flip` | Zero-gamma level in dollars, or none when it cannot be computed. |
| `time` | `time` | When the book was computed. A UTC `datetime` in Python and a `Date` in TypeScript. |
| `age` | `age` | Seconds since `time`, read now. |
| `for row in book` | `for (const row of book)` | Rows with `strike`, `call`, `put` and `total`, in dollars per $1 move. Calls are positive, puts negative, `total` is their sum. |
| `get(600)` | `get(600)` | The row at that strike, or none. |
| `strikes`, `calls`, `puts`, `totals` | `columns` | The same data as columns. TypeScript gives typed arrays. |
| `df()` | | A pandas DataFrame, when pandas is installed. |
| `meta` | `meta` | `book_id` (`bookId`), `version`, `changed` (the strikes that moved in this frame, all of them for a full book) and `reason` (why the server sent a full book, or none for an update). |

The book holds the 120 strikes with the largest gross GEX, `|call| + |put|`. Ties go to the lower strike. Gross is used so that a strike where a large call wall and a large put wall cancel out still makes the list. Membership is fixed between keyframes. The book carries no open interest. Use `get_gex` for that. Call and put values are single precision, as sent.

## Filters

Filters are keyword arguments in Python and an options object in TypeScript. Pass them to `stream.gex(...)` or later to `update(...)`. Nothing is sent to the server.

| Option | Keeps |
|---|---|
| `nearest=n` | The `n` strikes closest to spot. Ties go to the lower strike. |
| `within=0.02` | Strikes within 2 percent of spot: `\|strike / spot - 1\| <= 0.02`. |
| `between=(590, 610)` | Strikes from 590 to 610 dollars, both included. TypeScript: `between: [590, 610]`. |
| `top=n`, `by="gross"` | The `n` largest by `gross`, `net`, `call` or `put`. `net` is `\|call + put\|`. Combines with one of the options above and applies after it. |

Pass at most one of `nearest`, `within` and `between`. `update()` with no options goes back to the whole book. Filters choose among the book's 120 strikes and never add one the server did not send. So `nearest=20` can have gaps: a strike close to spot with little GEX may not be in the book. Check the strikes rather than assuming even spacing. In a filtered book, `meta.changed` lists only the strikes in view.

## How the stream works

1. **Ticket.** The SDK calls `POST /v2/stream/ticket` with your key. A ticket expires 60 seconds after minting and authorizes one connection. The SDK mints a fresh ticket for every connect.
2. **Connect.** It opens `wss://api.itmatrixhq.com/v2/ws` with the subprotocol `itmatrixhq.v1`.
3. **Authenticate.** The first frame is JSON: `{"op":"auth","ticket":"...","proto":8,"encoding":"protobuf","client":"itmatrix-python/1.2.0"}`. The server answers `authed` or a refusal. The SDK waits 20 seconds for the answer and treats a longer wait as a failed connect. The client string is `itmatrix-python/<version>` for Python and `itmatrix-typescript/<version>` for TypeScript. SDK 1.2.0 sends `itmatrix-python/1.2.0` or `itmatrix-typescript/1.2.0`.
4. **Subscribe.** The SDK sends a `sub` control frame for topic `gex` with a symbol. Only `symbol` is accepted as a parameter. Control frames are JSON text. Data frames are binary protobuf.
5. **Keyframe, then updates.** The first frame for a subscription is a `GexStrikeBook` keyframe. It replaces all state for the symbol. Later frames are `GexStrikeBookUpdate` messages that carry only the strikes whose call or put GEX changed, plus spot, net GEX and flip point. Both sides are sent for every listed strike. A strike is listed when either side moved by more than 0.001 times the largest single side in the book.

The engine publishes about every 5 seconds today. We will tune the cadence and work on bringing it up. A keyframe is sent on subscribe, every 300 seconds, on resync, when the book is rebuilt and when the server has skipped updates for you. If more than 60 percent of the strikes changed, the server sends a keyframe instead of an update.

Explorer and Pro receive the same cadence. The rungs differ by limits only.

### Reconstruction rules

The SDK applies these rules. They are listed so you can reason about what you see in `meta`.

- A `GexStrikeBook` replaces state, once its own CRC checks out. It adopts the keyframe's `book_id` and `version`, whatever they were before. A keyframe that fails its CRC or is malformed is not adopted. The SDK asks for another.
- An update applies only if its `book_id` matches and its `version` is exactly one more than the current version. Its CRC must match the book after applying.
- A `version` at or below the current one is a duplicate and is ignored.
- Any other gap, a different `book_id` or a CRC mismatch makes the SDK send `{"op":"resync","id":"..."}`. It drops updates until the next keyframe arrives. The SDK sends at most one resync per subscription every 5.5 seconds, because the server honors one every 5 seconds.
- Keyframes and updates are checked the same way. The CRC is CRC-32 (IEEE, as in zlib). It covers, for each strike in ascending order, the strike in thousandths as a little-endian signed 64-bit integer, then the single-precision call GEX bits, then the single-precision put GEX bits, both little-endian. The derived total is not covered.
- A new `book_id` means discard everything. It follows a session roll, an engine restart or a member strike leaving the grid. An open interest change is an ordinary update.

There is no resume across reconnects. A reconnect costs one keyframe per subscription.

## Reconnect and backoff

One SDK client owns one connection and the live books on it. After a reconnect it resubscribes everything and each book receives a fresh keyframe. The status goes to `reconnecting` and back to `live`.

| Situation | SDK behavior |
|---|---|
| Network error, timeout, `5xx` or unexpected close | Reconnect with exponential backoff from 0.25 s to 10 s. Each wait is a random time from zero up to the current limit (full jitter). |
| `Retry-After` on a `429` or other mint answer | Wait that long, capped at 300 seconds, then retry. |
| Refused with `connection_limit` | Wait 30 seconds plus 0 to 50 percent random jitter, so 30 to 45 seconds. This applies even when `Retry-After` is shorter. |
| `bye` with `draining` | Wait for `reconnect_after_ms`, then reconnect. The server is restarting. |
| `bye` with `slow_consumer` | Close code 1013. Wait for `reconnect_after_ms`, then reconnect. |
| `bye` with `reauth` | Reconnect immediately. Sessions are re-authenticated at least every 15 minutes, so a revoked key or a changed plan takes effect within that time. |
| Any other `4xx` at the ticket mint, such as a revoked key | Terminal. An `error` event, then `closed`. `408` and `429` are retried. |
| Refused with `stream_preview`, `client_outdated`, `client_required`, `protobuf_required` or `proto_required` | Terminal. An `error` event, then `closed`. |
| `not_entitled_tier` or `invalid_params` at authentication | Terminal, whatever the reason. |
| A `nack` on one subscription, such as `subscription_limit` | That live book reports the error and closes. Other books on the connection continue. |

When the SDK gives up, every live book on the client reports the same `StreamError` with `terminal` set, then closes. The one exception is Python without the `stream` extra, which reports `ImportError` with the install command.

### Heartbeat

The server pings every 15 seconds and closes a connection that is silent for 45 seconds. The SDK answers each ping with `pong`. It also sends its own `{"op":"ping"}` every 20 seconds and treats 40 seconds without any message from the server, of any kind, as a dead connection. It then closes the socket and reconnects. The SDK does not use the WebSocket library's own ping.

### Close and refusal reasons

A refusal at the WebSocket arrives as `{"op":"nack","code":"...","reason":"..."}`, followed by close code 1008. The `reason` is a top-level field of the nack. `limit` is also top-level when the refusal is about a limit. The SDK exposes both on the `StreamError`, with the limit under `details`.

| Code | Reason | Meaning and what to do |
|---|---|---|
| `not_entitled_tier` | `stream_preview` | Key streaming is switched off for the account. Do not retry. Contact ITMatrixHQ. |
| `invalid_params` | `protobuf_required` | The auth frame did not request protobuf. Use the SDK. |
| `invalid_params` | `proto_required` | The protocol version was not 8. Upgrade the SDK to 1.2.0 or later. |
| `invalid_params` | `client_required` | The subprotocol or client name was missing. Use the SDK. |
| `invalid_params` | `client_outdated` | The SDK is older than the minimum version. Upgrade. |
| `quota_exceeded` | `connection_limit` | The account is at its connection limit. Close a connection or wait. The `limit` field gives the number. |
| `quota_exceeded` | `subscription_limit` | The account or connection is at its subscription limit. Unsubscribe something first. |
| `invalid_params` | `unsupported_param` | A subscription carried a parameter other than `symbol`. |
| `not_entitled_tier` | none | A topic other than `gex` was requested. Keys receive `gex` only. |
| `not_entitled_symbol` | none | Index symbols, SPX GEX included, are not available. |

One reason comes from the SDK itself and never from the server: `invalid_params` with the reason `api_key_required`, reported when `stream.gex` is used without an `itm_` key.

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

Open one live book per symbol and keep it open. Do not reopen it to get a fresh snapshot, because each open costs a keyframe. Read `latest` instead. Use one SDK client, which is one connection, and share it across your code. Open a second client only when you need more than 50 symbols. Reconnect storms are the main avoidable cost, so do not wrap the SDK in your own retry loop. Its backoff already follows the rules above.

As design estimates, a keyframe is about 1.6 KB and an update with 20 changed strikes is about 0.25 KB. Each changed strike costs 8 bytes of values, because both sides are sent. Measured figures will replace these once the stream has run in production. The stream does not use permessage-deflate.

## Reference

- Protobuf schema: `spec/proto/itmatrixhq/ws/v1/wire.proto`, also served at `/v2/ws-protocol.proto`. The book is `GexStrikeBook` and `GexStrikeBookUpdate`, `Frame` fields 23 and 24. `GexBook` and `GexBookUpdate` (fields 21 and 22) are an earlier draft. The server never sends them and the SDK does not decode them.
- Encodings: [transport.md](transport.md).
