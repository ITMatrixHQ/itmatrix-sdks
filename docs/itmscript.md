# Headless studies: SDK bars into the ITMScript runtime

**The ITMScript runtime is not published.** `@itmatrixhq/itmscript` is private today. This page is the
contract the SDK side already honours: what `getBars` hands you, what a study runtime
needs, and the three host responsibilities nothing downstream can resolve for you.

The checked version of everything below is [`ts/examples/headless-study.ts`](../ts/examples/headless-study.ts),
which typechecks in the gate against this repository's own `Bar` and nothing else.

## The join

A study computes over one symbol on one timeframe. The public `Bar` is already that
shape, so there is no adapter between the two:

```ts
import { ITMClient } from "@itmatrixhq/core";

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY! });
const { data: bars } = await client.getBars("SPY", {
  from: Date.parse("2026-09-01T13:30:00Z"),
  to: Date.parse("2026-09-19T20:00:00Z"),
  timeframe: "5m",
});

const result = run('show the 20 bar average of close', bars);
```

`bars` is `Bar[]`: `symbol`, `timeframe`, `ts_ms` (epoch ms, **bucket start**), `open`,
`high`, `low`, `close`, `volume`, plus optional `vwap` and `trade_count` the runtime
ignores. `timeframe` is one of `1s`, `1m`, `5m`, `1h`, `1d` — the backend parses exactly
that set and serialises the same enum back, so nothing wider can arrive.

Live, keep the plan and feed it:

```ts
const study = createStudy('show the 20 bar average of close', bars);
study.append(newBucket);        // a bar the tape has moved past
study.update(currentBucket);    // a correction to the newest bar
study.reset(reloadedHistory);   // a fresh window
```

`append` and `update` are not interchangeable. Appending a correction invents a bar the
market never printed, and the study will not tell you it happened — the plot will just be
one bucket long and slightly wrong.

## Three things the host has to resolve first

The runtime computes; it does not fetch, cache or decide who may see what. Each of these
is invisible until it produces a plausible-looking wrong answer.

**A 304 is cache revalidation, not an empty history.** Pass `ifNoneMatch` and a
`NotModified` comes back with `data: undefined`. That means *what you already hold is
still current* — feed the retained bars. Feeding `undefined`, or treating it as zero
bars, silently turns a valid study into a blank one.

**Pagination is the host's loop.** `getBars` answers one page: `limit` is capped by the
server and `meta.cursor` continues. A study run over page one is a study over a truncated
history, and a 20-bar average of 18 bars is a number, not an error. Drain the cursor
before the first `run`, bound the loop, and fail on a repeated cursor rather than looping.
`assertRunnable` in the example is the other half: one symbol, one timeframe, strictly
ascending `ts_ms`.

**Entitlement belongs to the credential, not the method.** `getBars` existing does not
mean your key may call it. Bars are an app-only route; a key without the entitlement gets
a 403 `not_entitled_tier` (or `attestation_required` for index symbols), and a symbol
outside your tier gets one too. Handle `ITMError.code` before you handle the data, and
keep whatever provenance the response carried — delayed, stale or partial — attached to
the result you show. A study drawn from partial data that says nothing about it is the
most expensive kind of wrong.

## Node

Node 22+. The runtime is dependency-free ESM with hand-written declarations and no build
step, so `node --experimental-strip-types` runs a TypeScript host directly. The SDK needs
nothing beyond a global `fetch`.

There is no Python path. **Native evaluators do not exist in any other language** — the
runtime is TypeScript only, and `itmatrix` (Python) hands you normalized bars and
stops there. A Python consumer
that wants a study today computes it itself over `get_bars(...).data`, or calls the
TypeScript runtime out of process.
