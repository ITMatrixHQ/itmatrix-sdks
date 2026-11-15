# Encodings

| Path | Encoding |
|---|---|
| REST default | JSON |
| High-volume REST opt-in | protobuf |
| WebSocket control | JSON |
| WebSocket data | protobuf (API keys: protobuf only, no JSON data plane) |

SDK types accept only `json` and `protobuf`.

For REST, protobuf is selected with `Accept: application/x-protobuf` on bars, GEX grid,
chain and the off-exchange views. Errors remain JSON. For streams, the SDK mints a
short-lived ticket at connection time and authenticates with protocol 8 and
`encoding: "protobuf"`. Control frames are JSON. Each binary data message is one
protobuf `Frame` carrying a GEX book.

### Protocol versions

| Principal | Protocol | Auth frame | Data |
|---|---|---|---|
| API key | 8 | `proto: 8`, `encoding: "protobuf"`, `client` | `GexStrikeBook` keyframes and `GexStrikeBookUpdate` deltas for `gex` only |

Keys must also negotiate the `itmatrixhq.v1` WebSocket subprotocol and name the client in the auth frame. The client string is `itmatrix-python/<version>` for Python and `itmatrix-typescript/<version>` for TypeScript, for example `"client": "itmatrix-python/1.2.0"`. The server refuses a keyed connection that omits any of these. Only an `itm_` key can stream. A keyed auth at any protocol other than 8 is refused with `proto_required`. Protocol 8 carries the v2 book: call and put GEX per strike, the total derived in the SDK, no open interest. API Explorer and API Pro keys can stream with SDK 1.2.0 or later, which opens a live book per symbol with `client.stream.gex(...)`. See [streaming.md](streaming.md).

The connection rules: The SDK waits 20 seconds for `authed`, sends its own JSON `{"op":"ping"}` every 20 seconds, treats 40 seconds of silence as a dead connection, and reconnects with full-jitter backoff. A reconnect that meets a terminal `4xx` at the ticket mint stops and raises instead of retrying. [streaming.md](streaming.md) lists the full rules.

The schemas are pinned under `spec/proto/`. TypeScript uses `bigint` and Python uses
`int` for protobuf `uint64`, preventing timestamp truncation.

## Custom codecs

`ClientOptions.codec` accepts a `MarketDataCodec` object, which declares its media type and decodes one of `gex`, `chain` or `bars` into the corresponding domain model. It is used by `getGex`, `getOptionChain` and `getBars`. The default is JSON; `transport: "protobuf"` installs the built-in protobuf codec. An injected codec stays owned by the caller and is never fetched automatically. JSON server fallbacks and JSON errors retain their normal handling. Streams always use protobuf.

Python uses arbitrary-precision integers. TypeScript domain methods reject a protobuf integer outside the safe number range; raw wire exports preserve bigint.
