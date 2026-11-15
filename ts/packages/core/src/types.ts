export type Transport = "json" | "protobuf";
export type Credential = string | (() => string | Promise<string>);
export type QueryValue = string | number | boolean | Date | readonly string[] | null | undefined;
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export type Query = Record<string, QueryValue>;

export interface Meta {
  caps?: unknown;
  cursor?: string | null;
  plane?: string | null;
  /**
   * Echo of the `dte` filter, present only when one was sent. The backend
   * omits the key entirely for an unfiltered read, so `undefined` means
   * "no filter", not "0".
   */
  dte?: number;
  [key: string]: unknown;
}

export interface ApiResult<T> {
  data: T;
  meta: Meta;
  status: number;
  etag?: string;
  requestId?: string;
}
export interface NotModified {
  data: undefined;
  meta: Meta;
  status: 304;
  etag?: string;
  requestId?: string;
  notModified: true;
}

/**
 * Per-call cancellation. The signal composes with the client's `timeoutMs`:
 * whichever fires first aborts the request, so passing one never *extends*
 * the client timeout. An aborted call rejects with the signal's reason
 * (`AbortError` by default), not an `ITMError` — abort is the caller's own
 * decision, not a server condition.
 */
export interface Cancellable {
  signal?: AbortSignal;
}

export interface Bar {
  symbol: string;
  timeframe: "1s" | "1m" | "5m" | "1h" | "1d";
  ts_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  vwap?: number | null;
  trade_count?: number | null;
}

/**
 * GEX values are dollars of dealer hedging per $1 move. Each `*_shares` field
 * is its dollar sibling in shares of the underlying; `null` means unknown.
 */
export interface GexGrid {
  spot: number | null;
  prior_close_spot: number | null;
  captured_at: number | null;
  net_gex: number;
  net_gex_shares?: number | null;
  flip_point: number | null;
  max_abs_gex: number;
  max_abs_gex_shares?: number | null;
  strikes: GexStrike[];
}

export interface Chain {
  spot: number | null;
  captured_at: number | null;
  partial: boolean;
  rows: OptionChainRow[];
}

export interface GexOptions extends Cancellable {
  at?: string | number | Date;
  tz?: string;
  expiries?: string | readonly string[];
  /**
   * Days-to-expiry filter. The backend implements `0` only: net GEX from
   * just the contracts expiring on the session itself. The value sent is
   * echoed back in `meta.dte`; no echo means no filter was applied.
   */
  dte?: number;
  top?: number;
  byExpiry?: boolean;
}

export interface BarsOptions extends Cancellable {
  from: string | number | Date;
  to: string | number | Date;
  timeframe?: Bar["timeframe"];
  source?: "trade" | "mid";
  /** Price axis: `"split"` (the default; every bar rescaled to today's share count) or `"none"` (as traded). */
  adj?: "split" | "none";
  tz?: string;
  limit?: number;
  cursor?: string;
}

/**
 * `GET /v2/gex/{symbol}/history` — a whole session's GEX captures in one call.
 *
 * `date` is the ET session, and `from`/`to` are `HH:MM` **exchange-local**
 * bounds on it, half-open `[from, to)`: `to:
 * "16:00"` drops the closing capture, so ask for `"16:05"` if you want it.
 * Narrow with `top` — a full day with no cap is measured at ~5.5 MB.
 */
export interface GexHistoryOptions extends Cancellable {
  date: string;
  from?: string;
  to?: string;
  byExpiry?: boolean;
  top?: number;
}

export interface GexReferenceOptions extends Cancellable {
  /** Requested exchange session, `YYYY-MM-DD`. */
  date: string;
  basis: "open" | "prev_close";
}

/** One `GET /v2/gex/{symbol}/history` capture, shaped like a grid read. */
export interface GexHistoryCapture {
  captured_at: number;
  spot: number | null;
  prior_close_spot: number | null;
  net_gex: number;
  net_gex_shares?: number | null;
  flip_point: number | null;
  strikes: GexStrike[];
}

export interface GexHistory {
  symbol: string;
  session_date: string;
  captures: GexHistoryCapture[];
}

export interface GexReferenceStrike extends GexStrike {
  /** `gex` in shares per $1 move: `gex` divided by the book's `spot`. */
  gex_shares: number;
}

export interface GexReference {
  symbol: string;
  basis: "open" | "prev_close";
  session_date: string;
  reference_session_date: string;
  captured_at: number | null;
  spot: number | null;
  net_gex: number | null;
  complete: boolean | null;
  contract_count: number | null;
  gamma_absent: number | null;
  strikes: GexReferenceStrike[];
}

export interface OffExchangeOptions extends Cancellable {
  from?: string | number | Date;
  to?: string | number | Date;
  tz?: string;
  limit?: number;
  cursor?: string;
}

export interface OffExchangeActivityRow {
  date: string;
  off_exch_volume: number;
  notional: number;
  trade_count: number;
  avg_size: number;
  vwap: number;
  off_exch_pct: number | null;
  consolidated_volume: number | null;
  consolidated_notional: number | null;
}

export interface OffExchangeConcentrationRow {
  date: string;
  bucket_midpoint: number;
  bucket_width: number;
  volume: number;
  notional: number;
  trade_count: number;
  distinct_sessions: number;
  rank_score: number;
  rank: number;
  bucket_count: number;
  distance_bps: number | null;
  lookback_sessions: number;
}

export interface OffExchangeProfileRow {
  date: string;
  price_thousandths: number;
  volume: number;
  notional: number;
  trade_count: number;
}

/**
 * One session's off-exchange volume at one price level, from
 * `GET /v2/darkpool/{symbol}/levels`. `price_thousandths` is the exact level
 * (`766295` = $766.295); volume is shares, notional dollars, and there is no
 * buy or sell side.
 */
export interface DarkPoolLevelRow {
  ticker: string;
  date: string;
  price_thousandths: number;
  volume: number;
  notional: number;
  trade_count: number;
}

/** Range and page for dark-pool levels; without `from`/`to`, the latest session. */
export type DarkPoolLevelsOptions = OffExchangeOptions;

export interface OffExchangeCompositionRow {
  date: string;
  category: string;
  volume: number;
  notional: number;
  trade_count: number;
  share_of_off_exch_notional: number;
}

export interface ChainOptions extends Cancellable {
  at?: string | number | Date;
  tz?: string;
  expiry?: string;
  strikeGte?: number;
  strikeLte?: number;
}

/**
 * `GET /v2/fundamentals/{symbol}/{dataset}` query. `timeframe` and `limit`
 * apply to the statement datasets (`income`, `balance-sheet`, and `limit`
 * also to `short-interest`); the backend ignores them elsewhere.
 */
export interface FundamentalsOptions extends Cancellable {
  timeframe?: "quarterly" | "annual" | "trailing_twelve_months";
  /** Rows, newest first, 1..=50 (backend default 8). */
  limit?: number;
  /**
   * `income` and `balance-sheet` only. `"latest"` (the default) is the newest,
   * restated values; `"as_reported"` is what had been observed at `asOf`, the
   * only view a backtest or screen should use.
   */
  vintage?: "latest" | "as_reported";
  /**
   * With `vintage: "as_reported"`: the instant to read the history at, an
   * RFC 3339 timestamp or a `YYYY-MM-DD` date (the end of that UTC day).
   * Refused before any request without `vintage: "as_reported"`.
   */
  asOf?: string | Date;
  /** Conditional read; a 304 comes back as `NotModified`. */
  ifNoneMatch?: string;
}

/** Options for a plain conditional read: an ETag and a cancellation signal. */
export interface ConditionalOptions extends Cancellable {
  ifNoneMatch?: string;
}

export type WireData<T extends Transport, Json, Proto> =
  T extends "protobuf" ? Proto : Json;

/** Strikes and strike filters use integer thousandths: 600_000 means $600. */
export interface GexStrike {
  strike: number;
  gex: number;
  /** `gex` in shares of the underlying per $1 move; `null` when unknown. */
  gex_shares?: number | null;
  gex_0dte?: number;
  gex_0dte_shares?: number;
  call_oi?: number;
  put_oi?: number;
  delta_adj: number | null;
  delta_adj_shares?: number | null;
  expiry?: string;
}
export interface OptionContract {
  /** Absent when the negotiated codec does not carry the root identity. */
  underlying?: string;
  expiry: string;
  right: "call" | "put";
  /** Integer thousandths of a dollar. */
  strike: number;
}
export interface OptionChainRow {
  contract: OptionContract;
  oi?: number;
  volume?: number;
  bid?: number;
  ask?: number;
  last?: number;
  fmv?: number;
  iv?: number;
  delta?: number;
  gamma?: number;
  theta?: number;
  vega?: number;
  shares_per_contract: number;
}
export interface SymbolInfo {
  symbol: string;
  name: string;
  class: string;
  chain_policy: { window_days: number; tenor_frame: string };
  min_tier: string;
  requires_attestation: boolean;
}
export interface SymbolIdentity {
  valid: boolean;
  symbol: string;
  name: string | null;
  class: string | null;
  instrument_type: string;
  instrument_type_source: string | null;
  instrument_type_as_of: string | null;
}

export interface Quote {
  symbol: string;
  spot: number | null;
  change_pct: number | null;
  net_gex: number | null;
  gex_change: number | null;
  volume: number | null;
  official_close: number | null;
  session_date: string | null;
  event_ts_ms: number | null;
}

/** Epoch milliseconds, dollar price, source name, sequence number. */
