# spec/proto — public protobuf schemas

One schema for the WebSocket and one for REST, shared by every SDK language so the
wire cannot drift between them.

- `itmatrixhq/ws/v1/wire.proto`: the GEX books an API key streams. Each binary message
  is one `Frame`.
- `itmatrixhq/rest/v1/wire.proto`: REST bodies for bars, GEX grid, chain and the
  off-exchange views under `Accept: application/x-protobuf`.

The API serves the same files at `/v2/ws-protocol.proto` and `/v2/rest-protocol.proto`;
`scripts/check-contract.sh` asserts they are byte-identical to these copies.
