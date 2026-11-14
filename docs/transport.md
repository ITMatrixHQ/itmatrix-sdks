# Encodings

| Path | Encoding |
|---|---|
| REST default | JSON |
| High-volume REST opt-in | protobuf |
| WebSocket control | JSON |
| WebSocket data | protobuf (API keys: protobuf only, no JSON data plane) |

SDK types accept only `json` and `protobuf`.

For REST, protobuf is selected with `Accept: application/x-protobuf` on bars, replay,
GEX grid, chain and the off-exchange views. Errors remain JSON. For streams, the SDK mints
a short-lived ticket at connection time and authenticates with protocol 3 and
`encoding: "protobuf"`. Control frames remain JSON; each binary data message is one
protobuf `Frame`.

### Protocol versions

| Principal | Protocol | Auth frame | Data |
|---|---|---|---|
| Token (signed-in session) | 3 | `proto: 3`, `encoding: "protobuf"` | Frames for each topic |
| API key | 8 | `proto: 8`, `encoding: "protobuf"`, `client` | `GexStrikeBook` keyframes and `GexStrikeBookUpdate` deltas for `gex` only |

Keys must also negotiate the `itmatrixhq.v1` WebSocket subprotocol and name the client in the auth frame. The client string is `itmatrix-python/<version>` for Python and `itmatrix-typescript/<version>` for TypeScript, for example `"client": "itmatrix-python/1.1.0"`. The server refuses a keyed connection that omits any of these. The SDK sends protocol 8 only when the credential is an `itm_` key, so token behavior on the wire is the same as in 1.0.0. A keyed auth at any protocol other than 8 is refused with `proto_required`. Protocol 8 carries the v2 book: call and put GEX per strike, the total derived in the SDK, no open interest. API Explorer and API Pro keys can stream with SDK 1.1.0 or later. See [streaming.md](streaming.md).

Both principals share the connection rules in 1.1.0. The SDK waits 20 seconds for `authed`, sends its own JSON `{"op":"ping"}` every 20 seconds, treats 40 seconds of silence as a dead connection, and reconnects with full-jitter backoff. A reconnect that meets a terminal `4xx` at the ticket mint stops and raises instead of retrying. [streaming.md](streaming.md) lists the full rules.

The schemas are pinned under `spec/proto/`. TypeScript uses `bigint` and Python uses
`int` for protobuf `uint64`, preventing replay sequence or timestamp truncation.

## Custom codecs

`ClientOptions.codec` accepts a `MarketDataCodec` object, which declares its media type and decodes one of `gex`, `chain`, `bars`, or `replay` into the corresponding domain model. It is used by `getGex`, `getOptionChain`, `getBars`, and `getReplay`. The default is JSON; `transport: "protobuf"` installs the built-in protobuf codec. An injected codec stays owned by the caller and is never fetched automatically. JSON server fallbacks and JSON errors retain their normal handling. Streams always use protobuf.

Python uses arbitrary-precision integers. TypeScript domain methods reject a protobuf integer outside the safe number range; raw wire exports preserve bigint.
