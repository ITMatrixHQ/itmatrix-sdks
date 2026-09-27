# TypeScript SDK

See the [package quickstart](packages/core/README.md) for ITMClient methods, public transports and models.

From this directory run `npm ci`, `npm run typecheck`, `npm test`, and `npm run build -ws`. `npm pack -w @itmatrixhq/core` creates the installable tarball without publishing. Tests use injected fetch/WebSocket implementations and public synthetic fixtures; no credentials or private repository are needed.

`examples/` holds runnable-shaped host code checked by `npm run typecheck`. It depends on
this repository only — never on the frontend monorepo — so an example cannot drift from
the models it demonstrates. `examples/headless-study.ts` is the checked half of
[docs/itmscript.md](../docs/itmscript.md).
