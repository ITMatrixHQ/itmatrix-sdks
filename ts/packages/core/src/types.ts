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

export interface Replay {
  session: string;
  fidelity: "ticks" | "bars1m";
  events: Array<ReplayTick | ReplayBar>;
  complete: boolean;
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
  tz?: string;
  limit?: number;
  cursor?: string;
}

/**
 * `GET /v2/gex/{symbol}/history` — a whole session's GEX captures in one call.
 *
 * `date` is the ET session, and `from`/`to` are `HH:MM` **exchange-local**
 * bounds on it, half-open `[from, to)` like the stocks replay window: `to:
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

export interface ReplayOptions extends Cancellable {
  date: string;
  from?: string;
  to?: string;
  tz?: string;
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

export interface OffExchangeCompositionRow {
  date: string;
  category: string;
  volume: number;
  notional: number;
  trade_count: number;
  share_of_off_exch_notional: number;
}

/** Off-exchange condition categories a served row can carry (backend enum). */
export type DarkpoolConditionCategory =
  | "regular_way" | "odd_lot" | "package_price" | "form_t"
  | "average_price" | "derivatively_priced" | "late_oos";

/**
 * Scanner sort fields served from the normalized `symbol_stats` partition.
 */
export type DarkpoolSort =
  | "rank" | "off_exch_notional" | "off_exch_volume" | "share" | "trade_count"
  | "z20" | "z60" | "pctile_20" | "rel_print_max" | "accel_5_20";

export interface DarkpoolDatasetOptions extends Cancellable {
  from?: string | number | Date;
  to?: string | number | Date;
  /** `today` or a session date (footprint/intraday). */
  date?: string | number | Date;
  tz?: string;
  /** concentration only: currently 20, the materialized lookback. */
  lookback?: number;
  /** Page size; the server caps it and echoes what it used in `meta.limit`. */
  limit?: number;
  /** Continuation token from a previous response's `meta.cursor`. */
  cursor?: string;
}

export interface DarkpoolScannerOptions extends Cancellable {
  date?: string | number | Date;
  tz?: string;
  sort?: DarkpoolSort;
  dir?: "asc" | "desc";
  /** Floor on the session's off-exchange VWAP. */
  min_price?: number;
  /** Floor on the session's consolidated notional. */
  min_dollar_liquidity?: number;
  /** Floor on the symbol's off-exchange notional for the session. */
  min_off_exch_notional?: number;
  /** Minimum trailing-20 percentile, 0..1. */
  pctile_gte?: number;
  /** Minimum session-max print relative to its trailing p90. */
  rel_print_gte?: number;
  limit?: number;
  cursor?: string;
}

export interface DarkpoolBreadthOptions extends Cancellable {
  from?: string | number | Date;
  to?: string | number | Date;
  tz?: string;
  limit?: number;
  cursor?: string;
}

/**
 * Freshness, carried by every dark-pool response. Render `session` as a
 * trading date; never a relative label.
 */
export interface DarkpoolMeta extends Meta {
  /** Latest completed session represented in the response, `YYYY-MM-DD`. */
  session: string | null;
  /** Latest session the dataset holds. */
  latest_session: string | null;
  /** RFC3339 instant the partition was derived. */
  as_of: string | null;
  /** Sessions available for this symbol (market-wide reads: for the dataset). */
  history_sessions: number | null;
}

/**
 * `daily.notional` and `stats.off_exch_notional` are the same quantity;
 * `daily` keeps the legacy name because it is already served. Not aliased.
 */
export interface DarkpoolDailyRow {
  ticker: string;
  date: string;
  off_exch_volume: number;
  notional: number;
  trade_count: number;
  avg_size: number;
  /** Computed over the VWAP-eligible subset — deliberately not notional/volume. */
  vwap: number;
  off_exch_pct: number | null;
  consolidated_volume: number;
  consolidated_notional: number;
}

export interface DarkpoolStatsRow {
  ticker: string;
  date: string;
  off_exch_notional: number;
  off_exch_volume: number;
  trade_count: number;
  share: number | null;
  session_vwap: number | null;
  share_median_20: number | null;
  share_median_60: number | null;
  notional_median_5: number | null;
  notional_median_20: number | null;
  notional_median_60: number | null;
  notional_mean_20: number | null;
  notional_std_20: number | null;
  notional_mean_60: number | null;
  notional_std_60: number | null;
  z20: number | null;
  z60: number | null;
  pctile_20: number | null;
  pctile_60: number | null;
  accel_5_20: number | null;
  accel_20_60: number | null;
  large_print_p90_20: number | null;
  large_print_count: number;
  rel_print_max: number | null;
  adv_consolidated_20: number | null;
  dollar_adv_20: number | null;
  sessions_20: number;
  sessions_60: number;
  source_has_consolidated: boolean;
  source_has_condition_category: boolean;
}

export interface DarkpoolScannerRow extends DarkpoolStatsRow { rank: number; }

export interface DarkpoolConcentrationRow {
  ticker: string;
  date: string;
  bucket_price: number;
  tick: number;
  volume: number;
  notional: number;
  trade_count: number;
  distinct_sessions: number;
  first_session: string;
  last_session: string;
  rank_score: number;
  bucket_rank: number;
  buckets_total: number;
  ref_price: number | null;
  ref_session: string;
  lookback_sessions: number;
  lookback_first_session: string;
  distance_bps: number | null;
}

export interface DarkpoolBreadthRow {
  date: string;
  median_share: number | null;
  share_deciles: Array<number | null> | null;
  symbols_above_p80_20: number;
  symbols_above_p80_60: number;
  symbols_z20_gte_2: number;
  total_off_exch_notional: number;
  total_off_exch_notional_all_symbols: number;
  accel_breadth: number | null;
  universe_size: number;
  universe_min_price: number;
  universe_min_sessions_20: number;
  universe_price_source: string;
  universe_etf_rule: string;
  symbols_in_session: number;
  symbols_excluded_price: number;
  symbols_excluded_history: number;
  symbols_with_share: number;
}

/** Single-session raw price view; `price_thousandths` is a tenth-of-a-cent key. */
export interface DarkpoolLevelRow {
  ticker: string;
  date: string;
  price_thousandths: number;
  volume: number;
  notional: number;
  trade_count: number;
}

export interface DarkpoolIntradayRow {
  ticker: string;
  date: string;
  ts: number;
  volume: number;
  notional: number;
  vwap: number;
}

/** The session's condition decomposition; reconciles to `daily` by definition. */
export interface DarkpoolCompositionRow {
  ticker: string;
  date: string;
  category: DarkpoolConditionCategory;
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

export interface PremiumBucket {
  session_date: string;
  ts_ms: number;
  call_premium_usd: number;
  put_premium_usd: number;
  call_prints: number;
  put_prints: number;
  call_contracts: number;
  put_contracts: number;
  call_ask_premium_usd?: number | null;
  call_bid_premium_usd?: number | null;
  put_ask_premium_usd?: number | null;
  put_bid_premium_usd?: number | null;
}

export interface PremiumData {
  symbol: string;
  bucket_ms: number;
  sessions: string[];
  buckets: PremiumBucket[];
  coverage_status: string;
}

/** Epoch milliseconds, dollar price, source name, sequence number. */
export type ReplayTick = readonly [number, number, string, number];
export interface ReplayBar {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number | null;
}
