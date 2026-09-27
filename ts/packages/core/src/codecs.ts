import * as wire from "./_wire/rest.js";
import type { Bar, Chain, GexGrid, Meta, Replay } from "./types.js";

export interface MarketDataModels {
  gex: GexGrid;
  chain: Chain;
  bars: Bar[];
  replay: Replay;
}
export interface CodecContext {
  symbol: string;
  timeframe?: Bar["timeframe"];
  session?: string;
}
/** Codec implementations are injected by the application, never downloaded. */
export interface MarketDataCodec {
  readonly mediaType: string;
  decode<K extends keyof MarketDataModels>(
    operation: K, bytes: Uint8Array, context: CodecContext,
  ): { data: MarketDataModels[K]; meta?: Meta };
}

/** Fail loudly instead of silently rounding a 64-bit wire integer. */
function integer(value: bigint): number {
  const number = Number(value);
  if (!Number.isSafeInteger(number)) throw new RangeError("wire integer exceeds JavaScript safe range; use restWire for bigint access");
  return number;
}
function optionalInteger(value?: bigint): number | undefined {
  return value === undefined ? undefined : integer(value);
}
function day(value: number): string {
  return new Date(value * 86_400_000).toISOString().slice(0, 10);
}
function scale(value: number): number {
  if (!Number.isFinite(value) || value <= 0) throw new RangeError("invalid wire price scale");
  return value;
}
function bar(row: wire.Bar, priceScale: number, context: CodecContext): Bar {
  return {
    symbol: context.symbol, timeframe: context.timeframe ?? "1m",
    ts_ms: integer(row.tsMs), open: row.o / priceScale, high: row.h / priceScale,
    low: row.l / priceScale, close: row.c / priceScale, volume: row.v ?? null,
    vwap: row.vwap === undefined ? undefined : row.vwap / priceScale, trade_count: row.n,
  };
}

function decode(operation: keyof MarketDataModels, bytes: Uint8Array, context: CodecContext):
  { data: MarketDataModels[keyof MarketDataModels]; meta?: Meta } {
  switch (operation) {
    case "gex": {
      const value = wire.GexGridResponse.decode(bytes);
      return { data: {
        spot: value.spot ?? null, prior_close_spot: value.priorCloseSpot ?? null,
        captured_at: optionalInteger(value.capturedAtMs) ?? null, net_gex: value.netGex,
        net_gex_shares: value.netGexShares ?? null,
        flip_point: value.flipPoint ?? null, max_abs_gex: value.maxAbsGex,
        max_abs_gex_shares: value.maxAbsGexShares ?? null,
        strikes: value.rows.map(row => ({
          strike: integer(row.strikeThousandths), gex: row.gex,
          gex_shares: row.gexShares ?? null,
          gex_0dte: row.gex0dte, gex_0dte_shares: row.gex0dteShares,
          call_oi: row.callOi, put_oi: row.putOi,
          delta_adj: row.deltaAdj ?? null, delta_adj_shares: row.deltaAdjShares ?? null,
          expiry: row.expiryEpochDay === undefined ? undefined : day(row.expiryEpochDay),
        })),
      }, meta: { adhoc: value.adhoc } };
    }
    case "chain": {
      const value = wire.ChainResponse.decode(bytes);
      return { data: {
        spot: value.spot ?? null, captured_at: optionalInteger(value.capturedAtMs) ?? null,
        partial: value.partial, rows: value.rows.map(row => {
          if (row.right !== 0 && row.right !== 1) throw new TypeError("unknown option right");
          return {
            contract: { expiry: day(row.expiryEpochDay),
              right: row.right === 0 ? "call" : "put", strike: integer(row.strikeThousandths) },
            oi: optionalInteger(row.oi), volume: optionalInteger(row.volume),
            bid: row.bid, ask: row.ask, last: row.last, fmv: row.fmv, iv: row.iv,
            delta: row.delta, gamma: row.gamma, theta: row.theta, vega: row.vega,
            shares_per_contract: row.sharesPerContract,
          };
        }),
      } };
    }
    case "bars": {
      const value = wire.BarsResponse.decode(bytes);
      const priceScale = scale(value.priceScale);
      return { data: value.bars.map(row => bar(row, priceScale, context)), meta: {
        cursor: value.cursor || null, complete_through: optionalInteger(value.completeThroughMs) ?? null,
        prior_close: value.priorCloseScaled === undefined ? undefined : value.priorCloseScaled / priceScale,
      } };
    }
    case "replay": {
      const value = wire.ReplayResponse.decode(bytes);
      const priceScale = scale(value.priceScale);
      if (value.fidelity !== 0 && value.fidelity !== 1) throw new TypeError("unknown replay fidelity");
      return { data: {
        session: context.session ?? "", fidelity: value.fidelity === 0 ? "ticks" : "bars1m",
        complete: value.complete,
        events: value.fidelity === 0 ? value.ticks.map(row => {
          const source = value.sources[row.sourceId];
          if (source === undefined) throw new TypeError("unknown replay tick source");
          return [integer(row.tsMs), row.priceScaled / priceScale, source, integer(row.seq)] as const;
        }) : value.bars.map(row => ({ ts: integer(row.tsMs), o: row.o / priceScale,
          h: row.h / priceScale, l: row.l / priceScale, c: row.c / priceScale, v: row.v ?? null })),
      } };
    }
  }
}

/** The documented public protobuf codec, normalized to the JSON domain models. */
export const publicProtobufCodec: MarketDataCodec = {
  mediaType: "application/x-protobuf",
  decode: decode as MarketDataCodec["decode"],
};
