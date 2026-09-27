import { publicProtobufCodec, type MarketDataCodec, type MarketDataModels, type CodecContext } from "./codecs.js";
import {
  BarsResponse,
  ChainResponse,
  GexGridResponse,
  ReplayResponse,
} from "./_wire/rest.js";
import { DEFAULT_BASE_URL } from "./config.js";
import { ITMError } from "./errors.js";
import { analyzeGex, type GexAnalysis } from "./analytics.js";
import { resolveBarWindow, validateBarWindow, type BarPeriod } from "./periods.js";
import {
  AccountResource,
  DarkpoolResource,
  EconomyResource,
  FlowResource,
  FundamentalsResource,
  InfoResource,
  JournalResource,
  MarketResource,
  ProtocolResource,
  ReferenceResource,
  type RequestOptions,
  type ResourceRequester,
  ScreenerResource,
  VolResource,
  WatchlistsResource,
} from "./resources.js";
import { Stream, type StreamConfig } from "./stream.js";
import type {
  ApiResult, Bar, BarsOptions, Chain, ChainOptions, Credential, GexGrid,
  GexHistory, GexHistoryOptions, GexOptions, GexReference, GexReferenceOptions, Meta, NotModified, Query, QueryValue, Replay, ReplayOptions,
  Transport, WireData, SymbolInfo, SymbolIdentity,
} from "./types.js";

export interface ClientOptions<T extends Transport = "json"> {
  /** API origin; defaults to {@link DEFAULT_BASE_URL}. */
  baseUrl?: string;
  transport?: T;
  apiKey?: Credential;
  token?: Credential;
  fetch?: typeof fetch;
  retries?: number;
  timeoutMs?: number;
  /** An application-owned codec for normalized market-data methods. */
  codec?: MarketDataCodec;
  stream?: Omit<StreamConfig, "url" | "ticket"> & { url?: string };
}

export interface PeriodBarsOptions {
  /** `YYYY-MM`, only for `calendar_month`; omitted means the current NY month. */
  month?: string;
  /** Clock injection for reproducible reads and tests. */
  now?: Date;
  timeframe?: Bar["timeframe"];
  source?: "trade" | "mid";
  limit?: number;
  cursor?: string;
  signal?: AbortSignal;
}

type Decoder<P> = (bytes: Uint8Array) => P;

/**
 * The only paths outside `/v2/` this client will request. Liveness and
 * readiness are served at the root by the API and are unauthenticated, so
 * `InfoResource` needs them; everything else must stay under `/v2/` so a
 * caller cannot wander off the documented surface by passing a path to
 * `request()`. An allowlist rather than a loosened prefix check: adding a path
 * here should be a decision someone makes on purpose.
 */
const ROOT_PATHS: ReadonlySet<string> = new Set(["/healthz", "/readyz"]);

const CLOSED = "ITMClient is closed; create a new client to make more requests";

/**
 * The ITMatrixHQ API client. Construct it with `new ITMClient()` and call it;
 * REST use needs no disposal. Call `close()` after using `stream`.
 */
export class ITMClient<T extends Transport = "json"> {
  readonly baseUrl: string;
  readonly transport: T;
  readonly account: AccountResource;
  readonly darkpool: DarkpoolResource;
  readonly offexchange: DarkpoolResource;
  readonly economy: EconomyResource;
  readonly flow: FlowResource;
  readonly fundamentals: FundamentalsResource;
  readonly info: InfoResource;
  readonly journal: JournalResource;
  readonly market: MarketResource;
  readonly protocols: ProtocolResource;
  readonly reference: ReferenceResource;
  readonly screener: ScreenerResource;
  readonly vol: VolResource;
  readonly watchlists: WatchlistsResource;
  private readonly credential?: Credential;
  private readonly fetcher: typeof fetch;
  private readonly retries: number;
  private readonly timeoutMs: number;
  private readonly codec?: MarketDataCodec;
  private readonly streamConfig: StreamConfig;
  private streamInstance?: Stream;
  private closed = false;

  /**
   * The WebSocket client, built on first access.
   *
   * Constructing it eagerly made every REST-only client set up a reconnect
   * policy and a ticket closure it would never use; worse, the protobuf frame
   * bindings it needs are the single largest module in this package, and a
   * bundle that only ever calls `getBars` had no way to drop them. The frame
   * codec now loads on `connect()` (see `stream.ts`), so a client that never
   * touches `stream` never pays for it. Reading this getter is still
   * synchronous and returns the same instance every time.
   */
  get stream(): Stream {
    if (this.closed) throw new TypeError(CLOSED);
    this.streamInstance ??= new Stream(this.streamConfig);
    return this.streamInstance;
  }

  /** True once `close()` has run. */
  get isClosed(): boolean {
    return this.closed;
  }

  /**
   * Release what the client holds and refuse further calls. Idempotent.
   *
   * REST calls hold nothing between requests, so a client used only for REST
   * needs no disposal: drop it and the process exits normally. Call `close()`
   * when you used `stream`, to end its subscriptions, socket and reconnect
   * timer, or to make later use of this client an error.
   */
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.streamInstance?.close();
  }

  constructor(options: ClientOptions<T> = {} as ClientOptions<T>) {
    if (options.transport !== undefined && !["json", "protobuf"].includes(options.transport)) {
      throw new TypeError(`unsupported transport ${String(options.transport)}`);
    }
    if (!Number.isInteger(options.retries ?? 2) || (options.retries ?? 2) < 0) {
      throw new TypeError("retries must be a non-negative integer");
    }
    if (options.apiKey !== undefined && options.token !== undefined) {
      throw new TypeError("configure apiKey or token, not both");
    }
    const availableFetch = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (!availableFetch) throw new TypeError("no fetch implementation is available");

    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    const origin = new URL(this.baseUrl);
    if (!["http:", "https:"].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash) {
      throw new TypeError("baseUrl must be an HTTP(S) URL without credentials, query, or fragment");
    }
    if (!Number.isFinite(options.timeoutMs ?? 30_000) || (options.timeoutMs ?? 30_000) <= 0) {
      throw new TypeError("timeoutMs must be a positive finite number");
    }
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.codec = options.codec ?? (options.transport === "protobuf" ? publicProtobufCodec : undefined);
    this.transport = (options.transport ?? "json") as T;
    this.credential = options.apiKey ?? options.token;
    this.fetcher = availableFetch;
    this.retries = options.retries ?? 2;

    const request = (<R>(
      path: string,
      requestOptions: RequestOptions = {},
    ) => this.request<R>(path, requestOptions)) as ResourceRequester;
    this.account = new AccountResource(request);
    const offexchangeWire = (<Json, Proto>(path: string, query: Query,
      decoder: Decoder<Proto>, signal?: AbortSignal) =>
      this.wire<Json, Proto>(path, query, decoder, signal)) as import("./resources.js").ResourceWireRequester;
    this.darkpool = new DarkpoolResource(offexchangeWire);
    this.offexchange = this.darkpool;
    this.economy = new EconomyResource(request);
    this.flow = new FlowResource(request);
    this.fundamentals = new FundamentalsResource(request);
    this.info = new InfoResource(request);
    this.journal = new JournalResource(request);
    this.market = new MarketResource(request);
    this.protocols = new ProtocolResource(request);
    this.reference = new ReferenceResource(request);
    this.screener = new ScreenerResource(request);
    this.vol = new VolResource(request);
    this.watchlists = new WatchlistsResource(request);

    this.streamConfig = {
      ...options.stream,
      url: options.stream?.url ?? websocketUrl(this.baseUrl),
      ticket: async () => (
        await this.send<{ ticket: string; exp: number }, never>(
          "/v2/stream/ticket", { method: "POST" }, undefined, true,
        ) as ApiResult<{ ticket: string; exp: number }>
      ).data,
    };
  }

  /** GEX in dollars of exposure per $1 move. Strike values are integer thousandths. */
  getGex(symbol: string, options: GexOptions = {}): Promise<ApiResult<GexGrid>> {
    return this.marketData("gex", `/v2/gex/${symbolPath(symbol)}/grid`, {
      at: options.at, tz: options.tz, expiries: options.expiries,
      dte: options.dte, top: options.top, by_expiry: options.byExpiry,
    }, { symbol: normalizeSymbol(symbol) }, options.signal);
  }

  /** Server net GEX/zero gamma plus ranked levels among returned strike rows. */
  async getGexAnalysis(
    symbol: string, options: GexOptions & { levels?: number } = {},
  ): Promise<ApiResult<GexAnalysis>> {
    const { levels, ...gexOptions } = options;
    const result = await this.getGex(symbol, gexOptions);
    return { ...result, data: analyzeGex(result.data, { levels }) };
  }

  /**
   * A whole session's GEX captures in one call — the replay day-bundle.
   *
   * `date` is the ET session; `from`/`to` are `HH:MM` exchange-local bounds,
   * half-open `[from, to)`, so `to: "16:00"` drops the closing capture. JSON
   * only: unlike the grid there is no protobuf body for this route, and the
   * response is large enough that `top` is usually the right call (a full
   * uncapped day measures ~5.5 MB against ~1 MB for a capped regular session).
   * Past sessions are ETag-stable, so pass `ifNoneMatch` through `request()`
   * if you are polling one.
   */
  getGexHistory(symbol: string, options: GexHistoryOptions): Promise<ApiResult<GexHistory>> {
    return this.request<GexHistory>(`/v2/gex/${symbolPath(symbol)}/history`, {
      query: {
        date: options.date, from: options.from, to: options.to,
        by_expiry: options.byExpiry, top: options.top,
      },
      signal: options.signal,
    });
  }

  /**
   * The exact persisted open or preceding-close GEX book. An unavailable book
   * has `captured_at: null` and `strikes: []`; no nearby session is substituted.
   */
  getGexReference(
    symbol: string,
    options: GexReferenceOptions,
  ): Promise<ApiResult<GexReference>> {
    return this.request<GexReference>(`/v2/gex/${symbolPath(symbol)}/reference`, {
      query: { date: options.date, basis: options.basis },
      signal: options.signal,
    });
  }

  getOptionChain(symbol: string, options: ChainOptions = {}): Promise<ApiResult<Chain>> {
    return this.marketData("chain", `/v2/chain/${symbolPath(symbol)}`, chainQuery(options),
      { symbol: normalizeSymbol(symbol) }, options.signal);
  }

  getBars(symbol: string, options: BarsOptions): Promise<ApiResult<Bar[]>> {
    return this.marketData("bars", `/v2/stocks/${symbolPath(symbol)}/bars`, {
      from: options.from, to: options.to, timeframe: options.timeframe,
      tz: options.tz, source: options.source, limit: options.limit, cursor: options.cursor,
    }, { symbol: normalizeSymbol(symbol), timeframe: options.timeframe }, options.signal);
  }

  /** New York calendar period; rejects spans the backend would silently cap. */
  getBarsForPeriod(
    symbol: string, period: BarPeriod, options: PeriodBarsOptions = {},
  ): Promise<ApiResult<Bar[]>> {
    const window = resolveBarWindow(period, { now: options.now, month: options.month });
    const timeframe = options.timeframe ?? (period === "today" ? "1m" : "1d");
    validateBarWindow(window, timeframe);
    return this.getBars(symbol, {
      from: window.from, to: window.to, tz: window.tz, timeframe,
      source: options.source, limit: options.limit, cursor: options.cursor,
      signal: options.signal,
    });
  }

  getReplay(symbol: string, options: ReplayOptions): Promise<ApiResult<Replay>> {
    return this.marketData("replay", `/v2/stocks/${symbolPath(symbol)}/replay`, {
      date: options.date, from: options.from, to: options.to, tz: options.tz,
    }, { symbol: normalizeSymbol(symbol), session: options.date }, options.signal);
  }

  listExpirations(symbol: string, options: ChainOptions = {}): Promise<ApiResult<string[]>> {
    return this.expirations(symbol, options);
  }

  listSymbols(symbolClass?: "equity" | "index"): Promise<ApiResult<SymbolInfo[]>> {
    return this.reference.list(symbolClass);
  }

  getSymbol(symbol: string): Promise<ApiResult<SymbolInfo>> {
    return this.reference.get(symbol);
  }

  lookupSymbol(query: string): Promise<ApiResult<SymbolIdentity>> {
    return this.reference.lookup(query);
  }

  private marketData<K extends keyof MarketDataModels>(
    operation: K, path: string, query: Query, context: CodecContext, signal?: AbortSignal,
  ): Promise<ApiResult<MarketDataModels[K]>> {
    return this.send<MarketDataModels[K], never>(path, { query, signal }, undefined, true,
      this.codec ? { codec: this.codec, operation, context } : undefined,
      path) as Promise<ApiResult<MarketDataModels[K]>>;
  }

  gex(symbol: string, options: GexOptions = {}):
    Promise<ApiResult<WireData<T, GexGrid, GexGridResponse>>> {
    return this.wire(`/v2/gex/${symbolPath(symbol)}/grid`, {
      at: options.at, tz: options.tz, expiries: options.expiries,
      dte: options.dte, top: options.top, by_expiry: options.byExpiry,
    }, GexGridResponse.decode, options.signal);
  }

  bars(symbol: string, options: BarsOptions):
    Promise<ApiResult<WireData<T, Bar[], BarsResponse>>> {
    return this.wire(`/v2/stocks/${symbolPath(symbol)}/bars`, {
      from: options.from, to: options.to, timeframe: options.timeframe,
      tz: options.tz, source: options.source, limit: options.limit,
      cursor: options.cursor,
    }, BarsResponse.decode, options.signal);
  }

  replay(symbol: string, options: ReplayOptions):
    Promise<ApiResult<WireData<T, Replay, ReplayResponse>>> {
    return this.wire(`/v2/stocks/${symbolPath(symbol)}/replay`, {
      date: options.date, from: options.from, to: options.to, tz: options.tz,
    }, ReplayResponse.decode, options.signal);
  }

  chain(symbol: string, options: ChainOptions = {}):
    Promise<ApiResult<WireData<T, Chain, ChainResponse>>> {
    return this.wire(`/v2/chain/${symbolPath(symbol)}`, chainQuery(options),
      ChainResponse.decode, options.signal);
  }

  expirations(
    symbol: string,
    options: ChainOptions = {},
  ): Promise<ApiResult<string[]>> {
    return this.send(
      `/v2/chain/${symbolPath(symbol)}/expirations`,
      { query: chainQuery(options), signal: options.signal },
      undefined,
      true,
      undefined,
      `/v2/chain/${symbolPath(symbol)}/expirations`,
    ) as Promise<ApiResult<string[]>>;
  }

  request<R>(
    path: string,
    options: RequestOptions & { ifNoneMatch: string },
  ): Promise<ApiResult<R> | NotModified>;
  request<R>(path: string, options?: RequestOptions): Promise<ApiResult<R>>;
  request<R>(
    path: string,
    options: RequestOptions = {},
  ): Promise<ApiResult<R> | NotModified> {
    return this.send<R, never>(path, options, undefined, true);
  }

  private wire<Json, Proto>(
    path: string, query: Query, decoder: Decoder<Proto>, signal?: AbortSignal,
  ): Promise<ApiResult<WireData<T, Json, Proto>>> {
    return this.send<Json, Proto>(path, { query, signal }, decoder, false, undefined, path) as
      Promise<ApiResult<WireData<T, Json, Proto>>>;
  }

  private async send<Json, Proto>(
    path: string,
    options: RequestOptions,
    decoder?: Decoder<Proto>,
    forceJson = false,
    modelCodec?: { codec: MarketDataCodec; operation: keyof MarketDataModels; context: CodecContext },
    typedRoute?: string,
  ): Promise<ApiResult<Json | Proto> | NotModified> {
    if (this.closed) throw new TypeError(CLOSED);
    const method = options.method ?? "GET";
    const protobuf = !forceJson && this.transport === "protobuf" && decoder !== undefined;
    if ((!path.startsWith("/v2/") && !ROOT_PATHS.has(path)) || path.includes("\\") || /[?#]/.test(path) ||
        path.split("/").some(part => [".", ".."].includes(decodeURIComponent(part)))) {
      throw new TypeError("request path must stay under /v2/; use query for parameters");
    }
    const url = this.baseUrl + withQuery(path, options.query);

    for (let attempt = 0; ; attempt += 1) {
      const headers = new Headers({
        Accept: modelCodec?.codec.mediaType ?? options.accept ??
          (protobuf ? "application/x-protobuf" : "application/json"),
      });
      const credential = await resolveCredential(this.credential);
      if (credential) headers.set("Authorization", `Bearer ${credential}`);
      if (options.ifNoneMatch) headers.set("If-None-Match", options.ifNoneMatch);
      const body = requestBody(options);
      if (body !== undefined) {
        headers.set("Content-Type", options.contentType ?? "application/json");
      }

      const response = await this.fetcher(url, { method, headers, body, redirect: "error",
        signal: deadline(this.timeoutMs, options.signal) });
      if (
        method === "GET" && attempt < this.retries &&
        [429, 502, 503, 504].includes(response.status)
      ) {
        await response.body?.cancel();
        await delay(retryDelay(response, attempt));
        continue;
      }

      const requestId = response.headers.get("x-request-id") ?? undefined;
      const etag = response.headers.get("etag") ?? undefined;
      if (response.status === 304) {
        return { data: undefined, meta: {}, status: 304, requestId, etag, notModified: true };
      }
      if (!response.ok) throw await ITMError.fromResponse(response);

      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (modelCodec && contentType.split(";")[0]?.trim() === modelCodec.codec.mediaType.toLowerCase()) {
        const decoded = modelCodec.codec.decode(modelCodec.operation,
          new Uint8Array(await response.arrayBuffer()), modelCodec.context);
        return { data: decoded.data as Json, meta: { ...binaryMeta(response.headers), ...decoded.meta },
          status: response.status, requestId, etag };
      }
      if (modelCodec && !contentType.includes("json")) throw new TypeError("unexpected market-data content type");
      if (contentType.startsWith("application/x-protobuf")) {
        if (!decoder) throw new TypeError("protobuf response received without a decoder");
        return {
          data: decoder(new Uint8Array(await response.arrayBuffer())),
          meta: binaryMeta(response.headers), status: response.status, requestId, etag,
        };
      }
      if (!contentType.includes("json")) {
        if (typedRoute !== undefined) {
          throw nonEnvelope(typedRoute, response.status, contentType, requestId);
        }
        return {
          data: await response.text() as Json,
          meta: {}, status: response.status, requestId, etag,
        };
      }

      const value = await response.json() as Json | { data: Json; meta?: Meta };
      if (!isEnvelope<Json>(value)) {
        // A method that promises a typed model has to get the `{data, meta}`
        // envelope the route documents. A 2xx that is not one — a proxy
        // interstitial, a cached login page, a gateway's own JSON — used to be
        // handed back cast to `Bar[]`, so the lie surfaced later as a property
        // access on undefined with nothing pointing at the route. `request()`
        // stays permissive on purpose: `/healthz`, `/readyz` and
        // `/v2/openapi.json` are legitimately un-enveloped bodies.
        if (typedRoute !== undefined) {
          throw nonEnvelope(typedRoute, response.status, contentType, requestId);
        }
        return { data: value, meta: {}, status: response.status, requestId, etag };
      }
      return {
        data: value.data, meta: value.meta ?? {},
        status: response.status, requestId, etag,
      };
    }
  }
}

/**
 * The effective abort signal for one attempt: the client's timeout, plus the
 * caller's signal when there is one. Composed rather than chosen, so a
 * per-call signal can only ever shorten the deadline.
 */
function deadline(timeoutMs: number, signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([timeout, signal]) : timeout;
}

/** A 2xx that is not the documented envelope, named by the route that served it. */
function nonEnvelope(
  route: string, status: number, contentType: string, requestId?: string,
): ITMError {
  return new ITMError({
    status,
    code: "internal",
    message: `${route} returned ${status} with a body that is not the documented ` +
      `{data, meta} envelope (content-type ${contentType || "absent"}); ` +
      "refusing to present it as a typed result",
    details: { route, contentType },
    requestId,
  });
}

function chainQuery(options: ChainOptions): Query {
  return {
    at: options.at,
    tz: options.tz,
    expiry: options.expiry,
    strike_gte: options.strikeGte,
    strike_lte: options.strikeLte,
  };
}

function requestBody(options: RequestOptions): BodyInit | undefined {
  if (options.body === undefined) return undefined;
  if (options.contentType && options.contentType !== "application/json") {
    if (typeof options.body !== "string") {
      throw new TypeError("non-JSON request bodies must be strings");
    }
    return options.body;
  }
  return JSON.stringify(options.body);
}

function normalizeSymbol(value: string): string {
  const symbol = value.trim().toUpperCase();
  if (!symbol) throw new TypeError("symbol cannot be empty");
  return symbol;
}

function symbolPath(value: string): string {
  return encodeURIComponent(normalizeSymbol(value));
}

function isEnvelope<T>(value: unknown): value is { data: T; meta?: Meta } {
  return value !== null && typeof value === "object" && "data" in value;
}

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined) params.set(key, queryValue(value));
  }
  const encoded = params.toString();
  return encoded ? `${path}?${encoded}` : path;
}

function queryValue(value: Exclude<QueryValue, null | undefined>): string {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.join(",");
  return String(value);
}

async function resolveCredential(value?: Credential): Promise<string | undefined> {
  return typeof value === "function" ? await value() : value;
}

function binaryMeta(headers: Headers): Meta {
  const extra = headerJson(headers.get("x-itm-extra"));
  const meta: Meta = extra && typeof extra === "object" && !Array.isArray(extra)
    ? { ...(extra as Record<string, unknown>) } : {};
  const plane = headers.get("x-itm-plane");
  const caps = headerJson(headers.get("x-itm-caps"));
  if (plane !== null) meta.plane = plane;
  if (caps !== undefined) meta.caps = caps;
  return meta;
}

function headerJson(value: string | null): unknown {
  if (value === null) return undefined;
  try { return JSON.parse(value) as unknown; } catch { return undefined; }
}

function retryDelay(response: Response, attempt: number): number {
  const retryAfter = Number(response.headers.get("retry-after") ?? "0");
  return retryAfter > 0 && Number.isFinite(retryAfter) ? Math.min(retryAfter * 1_000, 30_000) : Math.min(250 * 2 ** attempt, 2_000);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function websocketUrl(baseUrl: string): string {
  const url = new URL("/v2/ws", baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** Compatibility name from the initial SDK preview; prefer ITMClient. */
export { ITMClient as Client };
