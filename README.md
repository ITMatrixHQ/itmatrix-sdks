# ITMatrixHQ SDKs

Handwritten clients for the ITMatrixHQ API, with readable methods, domain models, response metadata and public wire formats.

Python: `pip install itmatrix` (import name `itmatrix`). TypeScript: `npm install @itmatrixhq/core`.

```python
import os
import itmatrix as itm

client = itm.ITMClient(api_key=os.environ["ITM_API_KEY"])
grid = client.get_gex("SPY", top=20)
print(grid.data.net_gex)
for row in grid.data.strikes:
    print(row.strike, row.gex)
```

A context manager is optional. `with itm.ITMClient(...) as client:` closes the
client when the block ends; without it, call `client.close()` to release the
connection pool at a point you choose, or let the client close itself when it is
garbage-collected or the interpreter exits. `AsyncITMClient` works the same way
with `async with` or `await client.aclose()`. In TypeScript, `new ITMClient()`
needs no disposal for REST; call `close()` after using `stream`.

`Client` and `AsyncClient` remain compatibility aliases in Python; TypeScript also retains `Client`.

Use `itm.AsyncITMClient` with `async with` for asynchronous requests and streams. Both clients expose `get_gex`, `get_option_chain`, `get_bars`, `get_replay`, `list_expirations`, `list_symbols` and `lookup_symbol`, plus grouped resources for the broader API.

Options-flow discovery is available through the grouped `flow` resource. It
is deliberately bounded: provide a ticker or premium floor for live large
trades, and an explicit session plus one of those constraints for EOD flow.

```python
large = await client.flow.large_trades(min_premium_usd=250_000, limit=25)
eod = await client.flow.eod(session="2026-09-25", symbol="SPY")
section = await client.flow.cross_section("SPY")
```

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const grid = await client.getGex("SPY", { top: 20 });
console.log(grid.data.net_gex, grid.meta.caps);
```

| Package | Install | Capabilities |
| --- | --- | --- |
| [Python](python/README.md) `itmatrix` | `pip install itmatrix` | Sync + async REST, public JSON/protobuf, async streams |
| [TypeScript](ts/README.md) `@itmatrixhq/core` | `npm install @itmatrixhq/core` | REST, public JSON/protobuf, streams |

Python and TypeScript's friendly market-data methods normalize JSON and protobuf into stable domain models. Low-level wire APIs remain available for callers that need exact protobuf fields. Missing values remain missing; timestamps and strike units are documented. Every result retains metadata, status, request ID and ETag. Entitlements are enforced by the server; methods for app-only data do not give an API key access to it.

## Streaming GEX with an API key

API Explorer and API Pro keys can stream live GEX over WebSocket with SDK 1.1.0 or later (`pip install -U "itmatrix[stream]"`, `npm i @itmatrixhq/core@latest`). The book updates about every 5 seconds today. The stream uses protobuf frames, a keyframe plus update model and the `itmatrixhq.v1` subprotocol, all of which the SDK handles. Explorer allows 5 connections and 25 subscriptions. Pro allows 100 connections and 1,000 subscriptions. Each connection holds up to 50.

```python
async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
    async with client.stream.gex_book("SPY", filter=filters.nearest(20)) as book:
        async for state in book:
            print(state.net_gex, state.flip_point, state.strikes, state.gex)
```

```ts
for await (const state of client.stream.gexBook("SPY", { filter: nearest(20) })) {
  console.log(state.netGex, state.flipPoint, state.strikes, state.gex);
}
```

Each strike carries call and put GEX, and `gex` is their sum. The filter picks among the book's 120 strikes, which are ranked by gross GEX, so `nearest(20)` can have gaps.

The stream requires the ITMatrixHQ SDK protocol. The server cannot prove that the SDK was used, and the requirement is not a security control. See [streaming](docs/streaming.md) for the protocol, limits, reconnect rules and close reasons.

One shared stream owns tickets, subscriptions, ping/pong and reconnection. It mints a new ticket when establishing a connection and never replaces a healthy socket just because its handshake ticket expired.

## Contributing and packaging

See [CONTRIBUTING.md](CONTRIBUTING.md), [contract notes](docs/contract.md), and the [agent skill](skills/external/itmatrix-api/SKILL.md). Run `scripts/check.sh` for both package gates, or pass `python` or `ts`. Pull requests are reviewed by hand; no CI runs on them yet. Each language directory is independently packageable; the shared repository keeps protocol reviews and fixtures together. Public source is MIT licensed.

Only protobuf wire bindings are generated. Methods and domain models are handwritten. A small request escape hatch covers unusual operations without forcing users through a generated endpoint forest.

## Custom codecs

The TypeScript client accepts an injected `MarketDataCodec` for its four friendly market-data REST methods. See [encodings](docs/transport.md).

Portable release candidates and dependency policy: [release guide](docs/release.md).
