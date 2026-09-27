# Encodings

| Path | Encoding |
|---|---|
| REST default | JSON |
| High-volume REST opt-in | protobuf |
| WebSocket control | JSON |
| WebSocket data | protobuf |

SDK types accept only `json` and `protobuf`.

For REST, protobuf is selected with `Accept: application/x-protobuf` on bars, replay,
GEX grid, chain and the off-exchange views. Errors remain JSON. For streams, the SDK mints
a short-lived ticket at connection time and authenticates with protocol 3 and
`encoding: "protobuf"`. Control frames remain JSON; each binary data message is one
protobuf `Frame`.

The schemas are pinned under `spec/proto/`. TypeScript uses `bigint` and Python uses
`int` for protobuf `uint64`, preventing replay sequence or timestamp truncation.

## Custom codecs

`ClientOptions.codec` accepts a `MarketDataCodec` object, which declares its media type and decodes one of `gex`, `chain`, `bars`, or `replay` into the corresponding domain model. It is used by `getGex`, `getOptionChain`, `getBars`, and `getReplay`. The default is JSON; `transport: "protobuf"` installs the built-in protobuf codec. An injected codec stays owned by the caller and is never fetched automatically. JSON server fallbacks and JSON errors retain their normal handling. Streams always use protobuf.

Python uses arbitrary-precision integers. TypeScript domain methods reject a protobuf integer outside the safe number range; raw wire exports preserve bigint.
