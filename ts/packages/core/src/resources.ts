import type {
  ApiResult,
  ConditionalOptions,
  FundamentalsOptions,
  NotModified,
  OffExchangeActivityRow,
  OffExchangeCompositionRow,
  OffExchangeConcentrationRow,
  OffExchangeOptions,
  OffExchangeProfileRow,
  PremiumData,
  Quote,
  JsonObject,
  JsonValue,
  Query,
  SymbolInfo,
  SymbolIdentity,
} from "./types.js";
import {
  OffExchangeActivityResponse,
  OffExchangeCompositionResponse,
  OffExchangeConcentrationResponse,
  OffExchangeProfileResponse,
} from "./_wire/rest.js";

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  body?: unknown;
  contentType?: string;
  accept?: string;
  ifNoneMatch?: string;
  /**
   * Per-call cancellation. Composed with the client's `timeoutMs` — whichever
   * fires first wins — so it can shorten a request but never lengthen it.
   */
  signal?: AbortSignal;
}

export interface ResourceRequester {
  <T>(path: string, options: RequestOptions & { ifNoneMatch: string }):
    Promise<ApiResult<T> | NotModified>;
  <T>(path: string, options?: RequestOptions): Promise<ApiResult<T>>;
}
export interface ResourceWireRequester {
  <Json, Proto>(path: string, query: Query, decoder: (bytes: Uint8Array) => Proto,
    signal?: AbortSignal): Promise<ApiResult<Json | Proto>>;
}

const id = (value: string): string => encodeURIComponent(value);
const get = <T>(
  request: ResourceRequester, path: string, query?: Query, signal?: AbortSignal,
) => request<T>(path, { query, signal });

/**
 * A read that may be conditional. `ifNoneMatch` is only put on the request
 * when the caller supplied one, so an unconditional call keeps its
 * unconditional shape on the wire.
 */
function conditional<T>(
  request: ResourceRequester, path: string, query?: Query,
  ifNoneMatch?: string, signal?: AbortSignal,
): Promise<ApiResult<T> | NotModified> {
  return ifNoneMatch === undefined
    ? request<T>(path, { query, signal })
    : request<T>(path, { query, ifNoneMatch, signal });
}

/**
 * Split a caller-facing options object into the query it contributes and the
 * transport-level options it does not. `signal` is the one field that must
 * never reach the wire: stringified into a query param it would silently send
 * `signal=[object AbortSignal]` and the server would ignore it, which is how a
 * cancellation gets lost.
 */
function splitOptions<O extends { signal?: AbortSignal }>(
  options: O,
): { query: Query; signal?: AbortSignal } {
  const { signal, ...rest } = options;
  return { query: rest as Query, signal };
}
const mutate = <T>(
  request: ResourceRequester,
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
) => request<T>(path, { method, body });

export type Classification = "professional" | "non_professional";
export type FundamentalsDataset =
  | "ratios" | "income" | "balance-sheet" | "float"
  | "short-interest" | "dividends" | "news";
export type ImportFormat = "jsonl" | "csv";

type FlowLargeTradesBase = {
  session?: string;
  limit?: number;
};
export type FlowLargeTradesOptions = FlowLargeTradesBase & (
  | { symbol: string; minPremiumUsd?: number }
  | { symbol?: string; minPremiumUsd: number }
);
export type FlowEodOptions = {
  session: string;
  fromMs?: number;
  toMs?: number;
  right?: "call" | "put";
  expiry?: string;
} & (
  | { symbol: string; minPremiumUsd?: number }
  | { symbol?: string; minPremiumUsd: number }
);
export interface FlowPremiumOptions {
  sessions?: number;
  bucket?: "5m";
  minPremiumUsd?: number;
  dte?: "all" | "zero" | "ex_zero";
  /** Inclusive source-event cursor in epoch milliseconds. */
  toMs?: number;
  signal?: AbortSignal;
}

export class AccountResource {
  constructor(private readonly request: ResourceRequester) {}

  classification(): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/v2/account/classification");
  }

  submitClassification(classification: Classification): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, "/v2/account/classification", "POST", { classification });
  }

  listKeys(): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/account/keys");
  }

  createKey(): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, "/v2/account/keys", "POST");
  }

  deleteKey(keyId: string): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, `/v2/account/keys/${id(keyId)}`, "DELETE");
  }

  usage(): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/v2/account/usage");
  }
}

/**
 * Synthetic off-exchange analytics. Every response carries freshness in `meta`
 * (`session`, `latest_session`, `as_of`, `history_sessions`) and, when the
 * result was truncated to a page, an opaque `meta.cursor` to pass back as
 * `cursor`. An empty `data` with a `meta.note` is an honest empty answer, not
 * a denial.
 *
 * Individual prints, blocks and evidence snapshots are app-only and are not
 * reachable through this public resource.
 */
export class OffExchangeResource {
  constructor(private readonly wire: ResourceWireRequester) {}

  private read<Json, Proto>(symbol: string, view: string, options: OffExchangeOptions,
    decoder: (bytes: Uint8Array) => Proto): Promise<ApiResult<Json | Proto>> {
    const { query, signal } = splitOptions(options);
    return this.wire(`/v2/offexchange/${id(symbol.toUpperCase())}/${view}`, query, decoder, signal);
  }

  activity(symbol: string, options: OffExchangeOptions = {}):
    Promise<ApiResult<OffExchangeActivityRow[] | OffExchangeActivityResponse>> {
    return this.read(symbol, "activity", options, OffExchangeActivityResponse.decode);
  }

  concentration(symbol: string, options: OffExchangeOptions = {}):
    Promise<ApiResult<OffExchangeConcentrationRow[] | OffExchangeConcentrationResponse>> {
    return this.read(symbol, "concentration", options, OffExchangeConcentrationResponse.decode);
  }

  profile(symbol: string, options: OffExchangeOptions = {}):
    Promise<ApiResult<OffExchangeProfileRow[] | OffExchangeProfileResponse>> {
    return this.read(symbol, "profile", options, OffExchangeProfileResponse.decode);
  }

  composition(symbol: string, options: OffExchangeOptions = {}):
    Promise<ApiResult<OffExchangeCompositionRow[] | OffExchangeCompositionResponse>> {
    return this.read(symbol, "composition", options, OffExchangeCompositionResponse.decode);
  }
}

/** @deprecated Use `OffExchangeResource`; this alias cannot access legacy evidence routes. */
export class DarkpoolResource extends OffExchangeResource {}

export class EconomyResource {
  constructor(private readonly request: ResourceRequester) {}

  rates(options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/economy/rates", options);
  }

  treasuryYields(): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/economy/treasury-yields");
  }
}

export class FlowResource {
  constructor(private readonly request: ResourceRequester) {}

  /** Newest grouped large trades, bounded to 100 and narrowed before paging. */
  largeTrades(options: FlowLargeTradesOptions): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/flow/large-trades", {
      symbol: options.symbol?.toUpperCase(),
      min_premium_usd: options.minPremiumUsd,
      session: options.session,
      limit: options.limit,
    });
  }

  /**
   * Five-minute premium over retained detected large-trade legs. App-only,
   * Pro, and CBOE-attested. Missing buckets are unknown, never synthetic zero.
   */
  premium(symbol: string, options: FlowPremiumOptions = {}): Promise<ApiResult<PremiumData>> {
    return get(this.request, "/v2/flow/premium", {
      symbol: symbol.toUpperCase(),
      sessions: options.sessions,
      bucket: options.bucket,
      min_premium_usd: options.minPremiumUsd,
      dte: options.dte,
      to_ms: options.toMs,
    }, options.signal);
  }

  crossSection(symbol: string, session?: string): Promise<ApiResult<JsonObject>> {
    return get(
      this.request,
      `/v2/flow/${id(symbol.toUpperCase())}/cross-section`,
      { session },
    );
  }

  /**
   * Every leg of every large trade seen on the bus this session, newest first.
   * In-memory and session-scoped: it starts empty each day and does not
   * backfill, so treat an empty `data` as "nothing yet today".
   */
  prints(options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/flow/prints", options);
  }

  /** Persisted large prints for an explicit session, bounded to 100 rows. */
  eod(options: FlowEodOptions): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/flow/eod", {
      session: options.session,
      symbol: options.symbol?.toUpperCase(),
      min_premium_usd: options.minPremiumUsd,
      from_ms: options.fromMs,
      to_ms: options.toMs,
      right: options.right,
      expiry: options.expiry,
    });
  }

  /** One contract's trade tape. `contract` is an OCC symbol. */
  optionTape(contract: string, options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(this.request, `/v2/options/${id(contract.toUpperCase())}/tape`, options);
  }

  /** One contract's volume-at-price footprint, bucketed by `tf`. */
  optionFootprint(contract: string, options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(
      this.request,
      `/v2/options/${id(contract.toUpperCase())}/footprint`,
      options,
    );
  }
}

/**
 * Our own implied-vol surface, out of the greeks engine (`site.vol`) rather
 * than a vendor's numbers — which is why it is ours to serve at all.
 */
export class VolResource {
  constructor(private readonly request: ResourceRequester) {}

  /** ATM term structure for an underlying. */
  term(symbol: string): Promise<ApiResult<JsonValue>> {
    return get(this.request, `/v2/options/${id(symbol.toUpperCase())}/vol/term`);
  }

  /** The vol surface for an underlying. */
  surface(symbol: string): Promise<ApiResult<JsonValue>> {
    return get(this.request, `/v2/options/${id(symbol.toUpperCase())}/vol/surface`);
  }

  /** Per-contract greeks. `contract` is an OCC symbol. */
  greeks(contract: string, session?: string): Promise<ApiResult<JsonValue>> {
    return get(
      this.request,
      `/v2/options/${id(contract.toUpperCase())}/greeks`,
      { session },
    );
  }
}

export class FundamentalsResource {
  constructor(private readonly request: ResourceRequester) {}

  /**
   * One fundamentals dataset. `timeframe` selects the statement period and
   * `limit` the row count for the statement datasets; both are ignored by the
   * backend for the others. Pass `ifNoneMatch` to revalidate a cached read —
   * the 304 comes back as `NotModified`, which is cache confirmation, not an
   * empty dataset.
   */
  get(
    symbol: string, dataset: FundamentalsDataset,
    options: FundamentalsOptions & { ifNoneMatch: string },
  ): Promise<ApiResult<JsonValue> | NotModified>;
  get(
    symbol: string, dataset: FundamentalsDataset, options?: FundamentalsOptions,
  ): Promise<ApiResult<JsonValue>>;
  get(
    symbol: string, dataset: FundamentalsDataset, options: FundamentalsOptions = {},
  ): Promise<ApiResult<JsonValue> | NotModified> {
    const { timeframe, limit, ifNoneMatch, signal } = options;
    return conditional<JsonValue>(
      this.request,
      `/v2/fundamentals/${id(symbol.toUpperCase())}/${id(dataset)}`,
      { timeframe, limit },
      ifNoneMatch,
      signal,
    );
  }

  newsDigest(
    query: Query & { kind: "ticker" | "brief" },
    options: ConditionalOptions & { ifNoneMatch: string },
  ): Promise<ApiResult<JsonValue> | NotModified>;
  newsDigest(
    query: Query & { kind: "ticker" | "brief" }, options?: ConditionalOptions,
  ): Promise<ApiResult<JsonValue>>;
  newsDigest(
    query: Query & { kind: "ticker" | "brief" }, options: ConditionalOptions = {},
  ): Promise<ApiResult<JsonValue> | NotModified> {
    return conditional<JsonValue>(
      this.request, "/v2/news/digest", query, options.ifNoneMatch, options.signal,
    );
  }

  news(
    query: Query, options: ConditionalOptions & { ifNoneMatch: string },
  ): Promise<ApiResult<JsonValue> | NotModified>;
  news(query?: Query, options?: ConditionalOptions): Promise<ApiResult<JsonValue>>;
  news(
    query: Query = {}, options: ConditionalOptions = {},
  ): Promise<ApiResult<JsonValue> | NotModified> {
    return conditional<JsonValue>(
      this.request, "/v2/news", query, options.ifNoneMatch, options.signal,
    );
  }
}

export class JournalResource {
  constructor(private readonly request: ResourceRequester) {}

  listAccounts(): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/journal/accounts");
  }

  createAccount(account: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, "/v2/journal/accounts", "POST", account);
  }

  putAccount(accountId: string, account: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(
      this.request,
      `/v2/journal/accounts/${id(accountId)}`,
      "PUT",
      account,
    );
  }

  patchAccount(accountId: string, patch: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(
      this.request,
      `/v2/journal/accounts/${id(accountId)}`,
      "PATCH",
      patch,
    );
  }

  deleteAccount(accountId: string): Promise<ApiResult<JsonObject>> {
    return mutate(
      this.request,
      `/v2/journal/accounts/${id(accountId)}`,
      "DELETE",
    );
  }

  listTrades(): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/journal/trades");
  }

  createTrade(trade: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, "/v2/journal/trades", "POST", trade);
  }

  putTrade(tradeId: string, trade: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, `/v2/journal/trades/${id(tradeId)}`, "PUT", trade);
  }

  patchTrade(tradeId: string, patch: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, `/v2/journal/trades/${id(tradeId)}`, "PATCH", patch);
  }

  deleteTrade(tradeId: string): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, `/v2/journal/trades/${id(tradeId)}`, "DELETE");
  }

  importTrades(
    contents: string,
    options: { importId: string; format?: ImportFormat },
  ): Promise<ApiResult<JsonObject>> {
    return this.request("/v2/journal/import", {
      method: "POST",
      query: { import_id: options.importId, format: options.format },
      body: contents,
      contentType: "text/plain",
    });
  }
}

export class MarketResource {
  constructor(private readonly request: ResourceRequester) {}

  quotes(symbols: string | readonly string[]): Promise<ApiResult<Quote[]>> {
    return get(this.request, "/v2/quotes", { symbols });
  }

  spot(symbol: string): Promise<ApiResult<JsonObject>> {
    return get(this.request, `/v2/symbols/${id(symbol.toUpperCase())}/spot`);
  }

  /**
   * OHLCV bars for a single option contract (OCC symbol) — the per-contract
   * counterpart to the client's `getBars`, which is underlying-scoped.
   */
  optionBars(contract: string, options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(this.request, `/v2/options/${id(contract.toUpperCase())}/bars`, options);
  }
}

export class ReferenceResource {
  lookup(query: string): Promise<ApiResult<SymbolIdentity>> {
    return get(this.request, "/v2/symbols/lookup", { q: query.trim() });
  }

  constructor(private readonly request: ResourceRequester) {}

  list(symbolClass?: "equity" | "index"): Promise<ApiResult<SymbolInfo[]>> {
    return get(this.request, "/v2/symbols", { class: symbolClass });
  }

  get(symbol: string): Promise<ApiResult<SymbolInfo>> {
    return get(this.request, `/v2/symbols/${id(symbol.toUpperCase())}`);
  }
}

export class ScreenerResource {
  constructor(private readonly request: ResourceRequester) {}

  movers(options: Query = {}): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/screener/movers", options);
  }

  sectors(date?: string): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/screener/sectors", { date });
  }
}

export class ProtocolResource {
  constructor(private readonly request: ResourceRequester) {}

  descriptor(proto = 3): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/v2/ws-protocol.json", { proto });
  }

  websocketSchema(): Promise<ApiResult<string>> {
    return this.request("/v2/ws-protocol.proto", { accept: "text/plain" });
  }

  restSchema(): Promise<ApiResult<string>> {
    return this.request("/v2/rest-protocol.proto", { accept: "text/plain" });
  }
}

/**
 * Service discovery: liveness, readiness and the spec itself.
 *
 * `/healthz` and `/readyz` are the only paths this SDK requests outside
 * `/v2/` — see `ROOT_PATHS` in client.ts, which allows exactly those two and
 * nothing else. They take no credential (the API declares them `public: true`,
 * skipping the auth extractor), so they work before a key exists, which is the
 * point: they are how a caller finds out whether the API is up and what it
 * currently serves.
 *
 * `/readyz` is the deploy contract, not a synonym for healthz: it reports
 * warm-up and returns non-2xx while the process is still filling caches.
 */
export class InfoResource {
  constructor(private readonly request: ResourceRequester) {}

  /** Liveness. 200 once the process is serving at all. */
  health(): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/healthz");
  }

  /** Readiness, including warm-up state — non-2xx means "not yet". */
  ready(): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/readyz");
  }

  /**
   * The live OpenAPI document. Its `info.version`, operation set and
   * `x-exposure` stamps are the authority on what this deployment serves —
   * worth reading rather than assuming when a call starts 404ing.
   */
  openapi(): Promise<ApiResult<JsonObject>> {
    return get(this.request, "/v2/openapi.json");
  }
}

export class WatchlistsResource {
  constructor(private readonly request: ResourceRequester) {}

  list(): Promise<ApiResult<JsonValue>> {
    return get(this.request, "/v2/watchlists");
  }

  put(watchlistId: string, watchlist: JsonObject): Promise<ApiResult<JsonObject>> {
    return mutate(
      this.request,
      `/v2/watchlists/${id(watchlistId)}`,
      "PUT",
      watchlist,
    );
  }

  delete(watchlistId: string): Promise<ApiResult<JsonObject>> {
    return mutate(this.request, `/v2/watchlists/${id(watchlistId)}`, "DELETE");
  }
}

export const SUPPORTED_OPERATIONS = [
  "account_getClassification",
  "account_submitClassification",
  "account_listKeys",
  "account_createKey",
  "account_deleteKey",
  "account_getUsage",
  "bars_getBars",
  "bars_getOptionBars",
  "bars_getReplay",
  "chain_getChain",
  "chain_getExpirations",
  "offexchange_getActivity",
  "offexchange_getConcentration",
  "offexchange_getProfile",
  "offexchange_getComposition",
  "economy_getRates",
  "flow_getCrossSection",
  "flow_getEod",
  "flow_getLargeTrades",
  "flow_getOptionFootprint",
  "flow_getOptionTape",
  "flow_getPremium",
  "flow_getPrints",
  "flow_getUoa",
  "flow_getUoaEvent",
  "economy_getTreasuryYields",
  "fundamentals_getDataset",
  "news_getNews",
  "news_getDigest",
  "gex_getGrid",
  "gex_getHistory",
  "gex_getReference",
  "journal_listAccounts",
  "journal_createAccount",
  "journal_putAccount",
  "journal_patchAccount",
  "journal_deleteAccount",
  "journal_importTrades",
  "journal_listTrades",
  "journal_createTrade",
  "journal_putTrade",
  "journal_patchTrade",
  "journal_deleteTrade",
  "market_getQuotes",
  "market_getSpot",
  "reference_listSymbols",
  "reference_getSymbol",
  "reference_lookupSymbol",
  "studies_postAuthor",
  "studies_postAuthorFeedback",
  "screener_getMovers",
  "screener_getSectors",
  "stream_createTicket",
  "stream_connectWs",
  "stream_getProtocol",
  "stream_getWsProtocolProto",
  "stream_getRestProtocolProto",
  "vol_getContractGreeks",
  "vol_getSurface",
  "vol_getTerm",
  "watchlists_listWatchlists",
  "watchlists_putWatchlist",
  "watchlists_deleteWatchlist",
] as const;

export const INTENTIONALLY_UNSUPPORTED_OPERATIONS = [
  "darkpool_getScanner", "darkpool_getDataset", "darkpool_getBreadth",
] as const;
