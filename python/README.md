# ITMatrixHQ Python SDK

```sh
pip install itmatrix
# For WebSocket streams:
pip install "itmatrix[stream]"
```

```python
import os
import itmatrix as itm

client = itm.ITMClient(api_key=os.environ["ITM_API_KEY"])
result = client.get_gex("SPY", top=10)
print(result.data.spot, result.data.net_gex)
for row in result.data.strikes:
    print(row.strike, row.gex, row.expiry)
```

No `with` block is needed. The same client works as a context manager, which
closes it when the block ends:

```python
with itm.ITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
    print(client.get_gex("SPY").data.net_gex)
```

`itmatrix` is the distribution and import name. Python 3.10+ is supported. To build
from source instead, run `pip install ./python` from the repository root.

## A small vocabulary

| Method | `result.data` |
| --- | --- |
| `get_gex(symbol, at=..., top=..., by_expiry=...)` | `GexGrid` and `GexStrike` values |
| `get_gex_reference(symbol, date=..., basis="open")` | Exact persisted reference book, or explicit unavailable values |
| `get_option_chain(symbol, expiry=..., strike_gte=...)` | `OptionChain` with contract/quote models |
| `list_expirations(symbol)` | ISO expiry date strings |
| `list_symbols(symbol_class="equity")` | `Symbol` values from the registry |
| `lookup_symbol("spy")` | `SymbolIdentity` with broad class and conservative instrument classification |
| `get_bars(symbol, from_=..., to=..., timeframe="1m")` | `Bars` with `bars`, `cursor`, and completeness |
| `get_gex_analysis(symbol)` | Server net GEX and zero gamma plus ranked **visible** positive/negative strike levels |
| `get_bars_for_period(symbol, "today")` | New York calendar-day intraday bars; longer named periods default to daily bars |

The convenience methods are local compositions; they add no API route.
`get_bars_for_period(symbol, "calendar_month", month="2024-02")` requests the
whole named month with daily bars; inspect the returned cursor for another page.
`resolve_bar_window` can be used without a
client. See [convenience semantics](../docs/convenience.md) before treating
visible GEX levels as full-chain levels or a month as a completed session set.

`Result` retains `meta`, HTTP `status`, `request_id`, and `etag`. Unknown metadata
is preserved. `result.raw` is the body as received, before typing: the JSON
envelope's `data`, or the protobuf message. A field the API adds after your
installed release is there before the SDK types it. Nullable measurements stay `None`; zero is a real value. Timestamps
are epoch milliseconds, dates are ISO strings, bar/quote prices are dollars, and
strike filters use integer thousandths of dollars. `row.strike` is a convenient
dollar value; `row.strike_thousandths` preserves the exact integer. GEX values
(`gex`, `net_gex`, `max_abs_gex`, `delta_adj`, `gex_0dte`) are dollars of dealer
hedging per $1 move. Each has a served `*_shares` sibling (`gex_shares`,
`net_gex_shares`, `max_abs_gex_shares`, `delta_adj_shares`, `gex_0dte_shares`) in
shares of the underlying per $1 move, on the grid, history and reference book;
`None` means the server does not know it.

The public protobuf representation omits an option contract's OSI root. Its
`underlying` is therefore `None` on that transport, rather than a guessed value.
The normalized model otherwise uses the same field names and units for JSON and
public protobuf. Set `transport="protobuf"` to negotiate public protobuf on the
bulk market-data and off-exchange endpoints. Streaming always uses the public protobuf protocol.

Account, journal, watchlists, economy, offexchange, market, reference, screener, flow,
fundamentals, vol, protocol and service-info operations are grouped on both
clients. Together with the client's own methods they model **every non-internal
`/v2` operation the contract declares**; the pre-`/v2` aliases are deliberately
not modelled.

`client.offexchange` is the typed synthetic surface: `activity`, `concentration`,
`profile`, and `composition`, plus `levels`, the off-exchange volume by price
level that an API key can read (`/v2/darkpool/{symbol}/levels`). `client.darkpool`
is a compatibility alias to the same safe resource. There is no generic dataset
method or raw-evidence route.

```python
with itm.ITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
    rates = client.economy.treasury_yields()
    trades = client.journal.list_trades()

    # A whole session's GEX captures. `to` is exclusive, so 16:00 drops the close.
    day = client.get_gex_history("SPY", date="2026-09-04", from_="09:25", to="16:05", top=120)

    # Exact immutable book; captured_at=None means unavailable, not zero.
    reference = client.get_gex_reference("SPY", date="2026-09-25", basis="prev_close")

    # Our own implied vol, out of the greeks engine.
    term = client.vol.term("SPY")

    # Off-exchange volume by price level over five sessions, first page.
    levels = client.offexchange.levels("NVDA", from_="2026-11-03", to="2026-11-07")

    # Prices as traded rather than split-adjusted.
    raw_bars = client.get_bars("NVDA", from_="2024-06-03", to="2024-06-14", timeframe="1d", adj="none")

    # The income statement as it had been reported on a past date.
    income = client.fundamentals.get("MSFT", "income", vintage="as_reported", as_of="2025-06-30")

    # Is the API up, and what does this deployment serve? No credential needed.
    ready = client.info.ready()
    spec = client.info.openapi()
```

`info.health()` and `info.ready()` are the only calls that leave `/v2/`: the
client keeps a two-entry allowlist for them and rejects every other path, so
`request()` cannot wander off the documented surface. `ready()` is the deploy
contract rather than a synonym for `health()` — it is non-2xx while the process
is still warming up.

Grouped resources and `request("/v2/...")` expose raw JSON data for less common
operations. Endpoint availability still depends on server entitlements.
Bars and option chains are public to API keys. Flow and off-exchange reads also need
a non-professional classification on the account. Without it a key gets
`403 attestation_required`. Index symbols are `403 not_entitled_symbol` for keys.

## Async and streaming

```python
import asyncio

async def main():
    async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
        spy, qqq = await asyncio.gather(
            client.get_gex("SPY"), client.get_gex("QQQ"))
        print(spy.data.net_gex, qqq.data.net_gex)

asyncio.run(main())
```

Subscriptions live on `AsyncITMClient.stream`; they are bounded async iterators of
public protobuf frames. See the [streaming guide](../docs/streaming.md) and the
`stream.subscribe(...)` / `stream.spot(symbol)` helpers. Close subscriptions with
`async with` or `aclose()`, and close the client with `async with` or `aclose()`. Falling behind the configured
queue limit raises an error rather than silently discarding market updates.

### Live GEX book (API key)

A live book is an object that keeps the newest GEX book for one symbol and calls
your handlers when it changes. You do not run a loop.

```python
import asyncio, os
import itmatrix as itm

def show(book: itm.Book) -> None:
    print(book.symbol, book.spot, book.net_gex, book.flip)
    for row in book:                       # rows in strike order
        print(row.strike, row.call, row.put, row.total)

async def main() -> None:
    async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
        spy = client.stream.gex("SPY", nearest=20)
        spy.on("update", show)
        spy.on("status", lambda status: print("status:", status))
        spy.on("error", lambda error: print("error:", error))
        await client.run()                 # until every book closes, Ctrl-C closes them

asyncio.run(main())
```

`client.stream.gex(symbol)` needs an `itm_` API key on API Explorer or API Pro and
`pip install -U "itmatrix[stream]"` (SDK 1.2.0 or later). It returns at once and
connects in the background. Every live book on a client shares one connection.

| Event | Fires when | Handler receives |
|---|---|---|
| `update` | A new book is ready, and right after `update(...)` changes the filter | an `itm.Book` |
| `status` | The connection state changes | `"connecting"`, `"live"`, `"reconnecting"` or `"closed"` |
| `error` | A handler raised, or the stream hit a problem | the exception (`itm.StreamError` for stream refusals) |

On `AsyncITMClient` a plain handler runs inline on the event loop, so keep it quick.
An `async def` handler runs as a task, one call at a time. Delivery is conflated: a
busy handler is next called with the newest book, never a backlog. A handler that
raises goes to `error` and never stops the stream.

Besides handlers: `spy.latest` is the newest book (read it any time), `spy.age` its
age in seconds, `await spy.ready()` the first book, `await spy.changed()` the next
one and `async for book in spy` iterates conflated books. `spy.update(within=0.05)`
changes the filter and `spy.close()` ends the book. `await client.run()` returns
when every live book has closed. Ctrl-C closes them and returns without a traceback.

A book has `symbol`, `spot`, `net_gex` (the whole-chain total), `flip` (or `None`),
`time` (UTC), `age` and rows of `itm.GexRow(strike, call, put, total)`. `get(600)`
finds one strike, `strikes`, `calls`, `puts` and `totals` are columns, and `df()`
returns a pandas DataFrame. `meta` holds `book_id`, `version`, `changed` (the strikes
that moved) and `reason`. The book holds the 120 strikes with the largest gross GEX
and carries no open interest.

Filters are keyword arguments: `nearest=n`, `within=0.02` (2% of spot) or
`between=(590, 610)`, optionally with `top=n` and `by="gross"`, `"net"`, `"call"` or
`"put"`. They choose among the book's 120 strikes, so `nearest=20` can have gaps.

The blocking `itm.ITMClient` has the same `client.stream.gex(...)` and `client.run()`.
Its `ready(timeout)` and `changed(timeout)` block. Its handlers run one at a time on
the book's own thread (`itmatrix-gex-handlers`), or on an executor you pass with
`spy.on("update", fn, executor=pool)`, and never on the network thread.

The SDK keeps the book correct. It checks a CRC on every frame, asks for a fresh
keyframe on any gap, backs off with jitter and resubscribes after a reconnect. A
refusal it cannot retry is an `error` with `terminal` set, then the book closes.
Recipes (flip alerts, many symbols, web handlers, pandas, reconnects, async I/O) are
in the [streaming guide](../docs/streaming.md#recipes). See also `examples/gex_stream.py`.

## Client lifecycle

The blocking `ITMClient` owns one persistent background event loop and HTTP pool;
it never reuses async connections across short-lived loops. Create it once and
reuse it. `close()` is optional:

- Call `close()` (or use `with`) when you want the loop thread and connections
  released at a known point, such as a long-running process that makes a client
  per task. It is safe to call more than once; a request after it raises
  `RuntimeError`, and `client.closed` reports it.
- A client you never close is closed when it is garbage-collected or when the
  interpreter exits. Its loop runs on a daemon thread, so it never keeps a script
  from exiting.

Blocking requests are serialized. Use `AsyncITMClient` for concurrent work; an async
client belongs to the loop where it is used. Without `async with`, call
`await client.aclose()` when you are done: async cleanup cannot run from a
finalizer, so an async client that owns its pool and is collected unclosed only
emits `ResourceWarning`. Injected async HTTP clients remain owned by the caller.

## Errors, retries, and configuration

```python
client = itm.ITMClient(api_key=os.environ["ITM_API_KEY"], timeout=15, retries=2)
try:
    grid = client.get_gex("SPY")
except itm.ITMError as error:
    print(error.code, error.status, error.request_id)
```

Only GET responses with status 429, 502, 503, or 504 are retried; mutations and
transport exceptions are not replayed. Retry waits are bounded at 30 seconds,
with exponential fallback. `timeout` is HTTPX's per-operation timeout, not an
end-to-end deadline including retries. HTTPX transport exceptions propagate.
Redirects are disabled even for injected clients. The `request` escape hatch
accepts only `/v2/` paths; query parameters go in `query={...}`. Credentials are
resolved for each attempt and never attached to arbitrary absolute URLs.

Every request names the SDK in `X-ITM-SDK: itmatrix-python/<version>`. When the
API answers `X-ITM-SDK-Current` with a newer minor or major release, the client
issues one `itm.ITMUpgradeWarning` per process with the upgrade command. It never
raises and patch releases never trigger it. Silence it with
`ITMATRIX_NO_UPGRADE_CHECK=1` or an ordinary `warnings` filter.

`base_url` must be an HTTP(S) origin and defaults to `itm.DEFAULT_BASE_URL`, the
one place the package defines it. `api_key` and `token` are mutually exclusive;
each accepts a string or refresh callback (sync or async). No credentials are
inferred from environment variables. Tests can inject `http_transport`.

## Studies and indicators

There is no native evaluator in Python. The ITMScript runtime is TypeScript, unpublished,
and has no Python port planned; this package hands you normalized bars and stops there.
A study over `get_bars(...).data` is either computed in your own code or run through the
TypeScript runtime out of process. The rules a host has to resolve either way — a 304 is
revalidation and not an empty history, pagination is the caller's loop, entitlement
belongs to the credential — are in [docs/itmscript.md](https://github.com/ITMatrixHQ/itmatrix-sdks/blob/master/docs/itmscript.md).

## Contributing

From `python/`, install with `pip install -e .`, run
`python -m unittest discover -s tests`, and build with `python -m build`.
The repository contract-coverage test also requires the sibling `spec/` directory.
Only the protobuf serializers in `itmatrix/_core/_wire` are generated. Public clients,
models, options, resources, and tests are hand maintained. Wheels and source
archives contain the single `itmatrix` import package and its `py.typed` marker.
