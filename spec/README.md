# spec/ — pinned public contract

| Artifact | What it is | Consumed by |
|---|---|---|
| `openapi.json` | Public subset of the served REST contract | SDK inventory tests |
| `proto/` | Public protobuf wire schemas, byte-identical to the served copies | Python and TypeScript bindings |

Refresh both with `scripts/fetch-spec.sh`. `scripts/public-spec.py` keeps the public
operations of the served OpenAPI document. Public packages ship JSON and protobuf
transports only.
