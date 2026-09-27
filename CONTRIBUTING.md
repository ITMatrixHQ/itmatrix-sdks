# Contributing

Contribution automation is not set up yet. Pull requests are welcome and are reviewed by hand; no CI runs on them, and there is no response-time commitment. Automated checks for contributions are coming.

Welcome to the ITMatrixHQ SDKs. The Python and TypeScript packages live together so a protocol change can be reviewed across languages in one pull request. Each package also builds independently. No private checkout, account or API key is needed for unit tests.

Start with the README in the language directory. Keep public methods handwritten and named for the task (`get_gex`, `getGex`), with typed options and domain models. Preserve response metadata, nullable values and integer precision. The only generated source is the two protobuf bindings in each of Python and TypeScript; never generate endpoint wrappers or a model file per schema.

A change should include a small example and tests for its observable behavior. For wire changes, use the public protobuf schemas and public synthetic fixtures, test JSON/protobuf equivalence, and cover malformed input. Wire work uses only the protobuf schemas in `spec/proto/` and synthetic fixtures.

Run `scripts/check.sh python` or `scripts/check.sh ts`; run `scripts/check.sh` for all packages. Use the prerequisites in each package README. Tests run against injected transports or a local HTTP server, not production. Integration tests must be explicitly configured by the operator.

The pinned OpenAPI document describes both public and app-only routes. App-only methods remain entitlement-gated by the server; the SDK does not grant access. Refresh the snapshot using `scripts/fetch-spec.sh` when the API changes, inspect the diff, and update the named resource methods. Runtime responses and public protobuf fixtures take precedence where OpenAPI uses a generic schema; document such discrepancies in `docs/contract.md`.

Open a pull request with the behavior changed and the checks you ran. Never include credentials or real account or market data in reports or fixtures.
