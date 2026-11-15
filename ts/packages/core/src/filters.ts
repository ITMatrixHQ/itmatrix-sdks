/**
 * Filters over a GEX book.
 *
 * Each filter is a pure function from a {@link GexBookState} to a view of it:
 * the same header (symbol, book id, version, time, spot, net GEX, flip point,
 * reason) with only the chosen strikes, still in ascending order. `changed`
 * holds the positions in the view that the latest frame updated. The input is
 * never modified.
 *
 * Filters choose among the strikes the book carries. The server keeps the 120
 * strikes with the largest gross GEX (`|call| + |put|`), so a near-spot strike
 * with little GEX can be missing, and `nearest(20)` can have gaps.
 *
 * ```ts
 * import { nearest } from "@itmatrixhq/core";
 * const book = client.stream.gexBook("SPY", { filter: nearest(20) });
 * for await (const view of book) console.log(view.strikes, view.gex);
 * ```
 */
import type { GexBookState } from "./gexBook.js";

/** A pure function from a book to a filtered view with the same header. */
export type GexBookFilter = (state: GexBookState) => GexBookState;

/** What {@link top} ranks by. `net` is `|call + put|`, `gross` is `|call| + |put|`. */
export type GexRankBy = "gross" | "net" | "call" | "put";

/** Every strike in the book. The view is the state itself. */
export function all(): GexBookFilter {
  return (state) => state;
}

/** The `n` strikes closest to spot. Ties go to the lower strike. */
export function nearest(n: number): GexBookFilter {
  const count = checkCount(n, "nearest");
  return (state) => {
    const order = indices(state).sort((a, b) =>
      Math.abs(state.strikes[a]! - state.spot) - Math.abs(state.strikes[b]! - state.spot)
      || a - b);
    return pick(state, order.slice(0, count));
  };
}

/**
 * Strikes within a fraction of spot: `|strike / spot - 1| <= p`. `withinPct(0.02)`
 * keeps strikes within 2% of spot. Empty when spot is not positive.
 */
export function withinPct(p: number): GexBookFilter {
  if (!Number.isFinite(p) || p < 0) {
    throw new RangeError("withinPct needs a finite fraction >= 0, for example 0.02");
  }
  return (state) => {
    const spot = state.spot;
    if (!(spot > 0)) return pick(state, []);
    return pick(state, indices(state).filter(
      (i) => Math.abs(state.strikes[i]! / spot - 1) <= p));
  };
}

/** Strikes from `lo` to `hi` dollars, both included. */
export function between(lo: number, hi: number): GexBookFilter {
  if (Number.isNaN(lo) || Number.isNaN(hi) || lo > hi) {
    throw new RangeError("between needs lo <= hi");
  }
  return (state) => pick(state, indices(state).filter(
    (i) => state.strikes[i]! >= lo && state.strikes[i]! <= hi));
}

/**
 * The `n` strikes with the largest GEX by `by` (default `gross`). `call` and
 * `put` rank by size, so the largest put wall comes first. Ties go to the
 * lower strike. The view stays in strike order.
 */
export function top(n: number, by: GexRankBy = "gross"): GexBookFilter {
  const count = checkCount(n, "top");
  const score = RANKS[by];
  if (score === undefined) {
    throw new RangeError('top ranks by "gross", "net", "call" or "put"');
  }
  return (state) => {
    const order = indices(state).sort((a, b) =>
      score(state, b) - score(state, a) || a - b);
    return pick(state, order.slice(0, count));
  };
}

const RANKS: Record<GexRankBy, (state: GexBookState, i: number) => number> = {
  gross: (s, i) => Math.abs(s.callGex[i]!) + Math.abs(s.putGex[i]!),
  net: (s, i) => Math.abs(s.gex[i]!),
  call: (s, i) => Math.abs(s.callGex[i]!),
  put: (s, i) => Math.abs(s.putGex[i]!),
};

function checkCount(n: number, name: string): number {
  if (!Number.isInteger(n) || n < 0) {
    throw new RangeError(`${name} needs a whole number of strikes >= 0`);
  }
  return n;
}

function indices(state: GexBookState): number[] {
  return Array.from({ length: state.strikes.length }, (_, i) => i);
}

/** The view over the given positions of the book, in strike order. */
function pick(state: GexBookState, chosen: number[]): GexBookState {
  const keep = Uint32Array.from(chosen).sort();
  const count = keep.length;
  // Book position -> view position + 1 (0 = not in the view).
  const position = new Int32Array(state.strikes.length);
  for (let at = 0; at < count; at += 1) position[keep[at]!] = at + 1;
  let changes = 0;
  for (const i of state.changed) if (position[i]! > 0) changes += 1;
  // One buffer per view, as for a full state.
  const buffer = new ArrayBuffer(32 * count + 4 * changes);
  const strikes = new Float64Array(buffer, 0, count);
  const strikesThousandths = new Float64Array(buffer, 8 * count, count);
  const gex = new Float64Array(buffer, 16 * count, count);
  const callGex = new Float32Array(buffer, 24 * count, count);
  const putGex = new Float32Array(buffer, 28 * count, count);
  const changed = new Uint32Array(buffer, 32 * count, changes);
  for (let at = 0; at < count; at += 1) {
    const i = keep[at]!;
    strikes[at] = state.strikes[i]!;
    strikesThousandths[at] = state.strikesThousandths[i]!;
    gex[at] = state.gex[i]!;
    callGex[at] = state.callGex[i]!;
    putGex[at] = state.putGex[i]!;
  }
  let next = 0;
  for (const i of state.changed) {
    const at = position[i]!;
    if (at > 0) changed[next++] = at - 1;
  }
  return Object.freeze({
    ...state,
    strikes,
    strikesThousandths,
    callGex,
    putGex,
    gex,
    changed,
  });
}

/**
 * Filter options for a live GEX book: at most one window (`nearest`, `within`
 * or `between`), optionally followed by `top`.
 */
export interface GexFilter {
  /** The `n` book strikes closest to spot. */
  nearest?: number;
  /** Book strikes within a fraction of spot: `0.05` keeps strikes within 5%. */
  within?: number;
  /** Book strikes from `lo` to `hi` dollars, both included. */
  between?: readonly [number, number];
  /** Then keep the `n` largest by `by` (default `gross`). */
  top?: number;
  /** What `top` ranks by. */
  by?: GexRankBy;
}

const FILTER_KEYS: ReadonlySet<string> = new Set(["nearest", "within", "between", "top", "by"]);

/**
 * Check filter options and build the filter. Returns `undefined` for the whole
 * book. Throws `TypeError` or `RangeError` naming the bad option.
 */
export function buildFilter(options: GexFilter = {}): GexBookFilter | undefined {
  if (options === null || typeof options !== "object") {
    throw new TypeError("filter options must be an object such as { nearest: 20 }");
  }
  for (const key of Object.keys(options)) {
    if (!FILTER_KEYS.has(key)) {
      throw new TypeError(`unknown filter option "${key}"; use nearest, within, between, top and by`);
    }
  }
  const windows = (["nearest", "within", "between"] as const).filter((k) => options[k] !== undefined);
  if (windows.length > 1) {
    throw new RangeError(`pass at most one of nearest, within and between, not ${windows.join(" and ")}`);
  }
  if (options.by !== undefined && options.top === undefined) {
    throw new RangeError("by applies to top: pass top with it");
  }
  const steps: GexBookFilter[] = [];
  if (options.nearest !== undefined) steps.push(nearest(options.nearest));
  if (options.within !== undefined) {
    if (typeof options.within !== "number") throw new TypeError("within must be a fraction of spot such as 0.05");
    steps.push(withinPct(options.within));
  }
  if (options.between !== undefined) {
    const range = options.between;
    if (!Array.isArray(range) || range.length !== 2 || typeof range[0] !== "number" || typeof range[1] !== "number") {
      throw new TypeError("between must be a pair of strikes such as [590, 610]");
    }
    steps.push(between(range[0], range[1]));
  }
  if (options.top !== undefined) steps.push(top(options.top, options.by ?? "gross"));
  if (steps.length === 0) return undefined;
  if (steps.length === 1) return steps[0];
  return (state) => steps.reduce((view, step) => step(view), state);
}
