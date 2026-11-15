# Contract notes

The OpenAPI snapshot is API contract `2.20.0`. The keyed GEX stream
(`stream_createTicket`, `stream_connectWs`) arrived in 2.18.0, and 2.20.0 serves the
v2 book to API Explorer and API Pro keys. Curated OPRA reads are public to API
Explorer and API Pro keys with a non-professional classification: grouped large trades, one-symbol cross-sections, and
persisted EOD large prints. Live raw prints, contract tape/footprints, and UOA
evidence remain app-only. The snapshot is a reference, not endpoint
code-generation input. Some routes reuse generic backend schemas that do not
describe their actual response shape:

`flow.premium` is also app-only: it is a first-party chart aggregate over
retained detected large-trade legs, not a keyed public OPRA product. It requires
site Basic or higher plus CBOE attestation and carries explicit coverage metadata. The exact
open/preceding-close GEX reference book is public to callers with full GEX depth.

The Python and TypeScript off-exchange resources expose four explicit typed
synthetic views (activity, concentration, profile, composition) and `levels`,
which reads `/v2/darkpool/{symbol}/levels`, the one dark-pool dataset an API key
can read. The other `/v2/darkpool` operations are app-only and have no callable
SDK method.

- GEX grid returns an object containing spot, capture metadata and strike rows; row delta adjustment is `delta_adj`.
- Option chain returns `{spot, captured_at, partial, rows}`, not the generic `ChainPayload` discriminated union.
- Domain models in the SDK follow the served route and the public REST protobuf schema. Python and TypeScript normalize their friendly methods to those models for either transport. Lower-level legacy methods remain available during migration.

A 304 result carries metadata and no new data. It is not a cached payload. The caller owns caching. API error codes, request identifiers, caps and cursor metadata are preserved.

## Units

GEX values are dollars of dealer hedging per $1 move in the underlying: `gex`,
`net_gex`, `max_abs_gex`, `delta_adj` and `gex_0dte` on the grid and in history
captures. Dollars remain the base unit. Shares are served too: each dollar field
has a `*_shares` sibling (`gex_shares`, `net_gex_shares`, `max_abs_gex_shares`,
`delta_adj_shares`, `gex_0dte_shares`) in shares of the underlying per $1 move,
on the grid, history captures and the reference book, over JSON and protobuf.
The server produces them; the SDKs return them as served and never compute
them. A share field is null/None when the server cannot produce it, never 0.

Units are part of the contract: timestamps ending in `_ms` are epoch milliseconds, option strikes use the server's milli-dollar integer representation where specified, and missing market values remain null/None. SDKs do not guess a price, infer a missing timestamp or silently truncate protobuf integers.

## Flow bounds

Flow reads are intentionally small. `large_trades` / `largeTrades` require a
symbol or minimum premium and return at most 100 grouped events. `eod` requires
an explicit session plus a symbol or minimum premium and returns at most 100
ranked prints. Cross-section is constrained by its required symbol path. The
SDK validates or type-checks these constraints before sending a request.

## Backend compatibility gate

The SDK does not define a replacement wire protocol. REST and WS protobuf pins are byte-identical to the API 2.20.0 served at api.itmatrixhq.com, checked on 2026-11-14 (76 public operations green). Run `ITM_API_URL=https://your-api-origin bash scripts/check-contract.sh` to check drift before refreshing bindings. The check also confirms that the served WS schema has `GexStrikeBook` and `GexStrikeBookUpdate` as `Frame` fields 23 and 24 and that the stream operations are live. Set `ITM_API_KEY` as well to run a short live smoke of the keyed GEX stream. Schema differences require review with the backend; changing generated SDK bindings alone does not migrate a deployed server. Existing JSON/protobuf dev smoke covers real GEX decoding; the schema comparison also covers the WS contract, but is not an end-to-end test of every stream topic.
