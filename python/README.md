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
| `get_replay(symbol, date="2026-09-04")` | `Replay` with `Tick` or `Bar` events |
| `get_gex_analysis(symbol)` | Server net GEX and zero gamma plus ranked **visible** positive/negative strike levels |
| `get_bars_for_period(symbol, "today")` | New York calendar-day intraday bars; longer named periods default to daily bars |

The convenience methods are local compositions; they add no API route.
`get_bars_for_period(symbol, "calendar_month", month="2024-02")` requests the
whole named month with daily bars; inspect the returned cursor for another page.
`resolve_bar_window` can be used without a
client. See [convenience semantics](../docs/convenience.md) before treating
visible GEX levels as full-chain levels or a month as a completed session set.

`Result` retains `meta`, HTTP `status`, `request_id`, and `etag`. Unknown metadata
is preserved. Nullable measurements stay `None`; zero is a real value. Timestamps
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
`profile`, and `composition`. `client.darkpool` is a compatibility alias to the
same safe resource. There is no generic dataset method or raw-evidence route.

```python
with itm.ITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
    rates = client.economy.treasury_yields()
    trades = client.journal.list_trades()
    news = client.fundamentals.digest(kind="brief", scope="market")

    # A whole session's GEX captures. `to` is exclusive, so 16:00 drops the close.
    day = client.get_gex_history("SPY", date="2026-09-04", from_="09:25", to="16:05", top=120)

    # Exact immutable book; captured_at=None means unavailable, not zero.
    reference = client.get_gex_reference("SPY", date="2026-09-25", basis="prev_close")

    # First-party app session only: Pro + CBOE attestation, explicitly partial.
    premium = client.flow.premium("SPY", sessions=2, dte="zero", min_premium_usd=100_000)

    # Our own implied vol, out of the greeks engine.
    term = client.vol.term("SPY")

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
operations. Endpoint availability still depends on server entitlements; installing
an SDK does not grant app-only products such as bars or chain data.

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
public protobuf frames. See the repository streaming documentation and the
`stream.gex(symbol)` / `stream.spot(symbol)` helpers. Close subscriptions with
`async with` or `aclose()`, and close the client with `async with` or `aclose()`. Falling behind the configured
queue limit raises an error rather than silently discarding market updates.

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
