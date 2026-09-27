# spec/ — pinned public contract

| Artifact | What it is | Consumed by |
|---|---|---|
| `openapi.json` | Public subset of the served REST contract | SDK inventory tests |
| `proto/` | Public protobuf wire schemas, byte-identical to the served copies | Python and TypeScript bindings |

Refresh both with `scripts/fetch-spec.sh`; `scripts/public-spec.py` reduces the served
OpenAPI document to public and app-only operations. Public packages ship JSON and
protobuf transports only. First-party binary codecs are not part of this repository:
no implementations, schemas, vectors or layouts.
