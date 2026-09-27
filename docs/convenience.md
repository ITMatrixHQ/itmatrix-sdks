# Convenience and local analytics

The public SDK should answer common questions without making callers assemble
wire fields or duplicate deterministic calculations. It must keep the backend's
market-data authority and entitlement boundary: local helpers preserve source,
session, capture, coverage and null values instead of inventing a replacement.

## Patterns worth adopting

| Provider documentation | Useful pattern | ITMatrixHQ application |
| --- | --- | --- |
| [Alpaca Python historical data](https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html) and [bar periods](https://docs.alpaca.markets/us/reference/stockbarsingle-1) | Latest snapshot and monthly bar concepts above raw range requests | Period windows and, later, honest monthly rollups |
| [Databento historical examples](https://databento.com/docs/examples/basics-historical/requesting) | Symbology resolution and tabular conversion reduce caller glue | Typed symbol workflows and opt-in data-frame/table adapters |
| [QuantConnect indicators](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/key-concepts) | One indicator works on fetched history or a live update stream | Pure analytics functions that also back optional client methods |

These are design inferences from the cited provider docs, not claims that their
packages calculate our metrics.

## First slice on this branch

- `analyze_gex` / `analyzeGex` is pure and works with an already fetched grid.
  `get_gex_analysis` / `getGexAnalysis` fetches one grid and preserves the
  original result metadata. It labels the sign of server `net_gex`, carries
  server `flip_point` as nullable `zero_gamma`, and ranks positive/negative
  exposure by strike among **returned rows**. A `by_expiry` grid is grouped by
  strike before ranking. The original grid remains available on the result.
  The SDK never sums a trimmed list to replace the server's full-curve net
  GEX. Tier limits and `top` can hide stronger levels; the returned rankings
  are not claimed to be full-chain walls.
- `resolve_bar_window` / `resolveBarWindow` and
  `get_bars_for_period` / `getBarsForPeriod` provide `today`, `week_to_date`,
  `month_to_date`, `year_to_date`, and explicit `calendar_month`. These are
  **America/New_York calendar dates**, including weekends and holidays, and
  the backend expands a date-only `to` through the named day. Today defaults
  to 1-minute bars; longer periods default to daily bars. A timeframe override
  that could cross the backend's silent range clamp fails locally.
  The method returns one page and preserves its cursor; it makes no claim
  that all bars for the period have been collected.

## Next useful helpers, with the rule each needs

1. **Monthly OHLCV rollup:** derive from complete daily pages; state whether
   the month is in progress and whether any source day is missing. Do not
   manufacture zero-volume days or imply split adjustment the bars endpoint
   did not supply.
2. **Bounded pagination:** async iterator and explicit `max_pages`/`max_rows`
   collector for bars and off-exchange history. Carry each page's `meta` and
   stop on repeated cursors; never silently return the first page as a month.
3. **GEX change from reference:** combine one grid with an exact persisted
   open/preceding-close book only when the requested session and capture
   provenance agree. Keep unavailable or incomplete references nullable.
4. **Chain concentration and put/call summaries:** only over a declared
   complete chain or expiry slice. A partial chain cannot support a marketwide
   put/call ratio.
5. **Watchlist research snapshot:** compose quote, GEX and reference reads with
   independent timestamps and availability flags. A mixed-time snapshot is
   useful if every component's age is visible; it is not one atomic capture.

This layer makes no `/v2` shape change and does not add a new entitlement path.
