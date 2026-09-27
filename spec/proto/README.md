# spec/proto — public protobuf schemas

One schema per plane, shared by every SDK language so the external wire cannot drift
between them.

- `itmatrixhq/ws/v1/wire.proto` — WebSocket transport. Negotiated per connection with
  `encoding: "protobuf"` on the `auth` frame; each binary message is one `Frame`.
- `itmatrixhq/rest/v1/wire.proto` — REST bodies for bars, replay, GEX grid, chain and the
  off-exchange views under `Accept: application/x-protobuf`.

The API serves the same files at `/v2/ws-protocol.proto` and `/v2/rest-protocol.proto`;
`scripts/check-contract.sh` asserts they are byte-identical to these copies.
