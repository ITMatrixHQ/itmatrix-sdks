import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { streamWire } from "../src/index.js";
import { all, between, buildFilter, nearest, top, withinPct } from "../src/filters.js";
import type { GexBookState } from "../src/gexBook.js";
import { GexBookTracker } from "../src/gexBook.js";

const VECTORS = JSON.parse(
  readFileSync(new URL("./fixtures/gex_strike_book_vectors.json", import.meta.url), "utf8"),
) as { steps: Array<{ name: string; frame_hex: string }> };
const bytesOf = (hex: string): Uint8Array =>
  Uint8Array.from(hex.match(/../g) ?? [], (pair) => parseInt(pair, 16));

/** The book after the keyframe and update_1 (version 8, spot 601.5, changed [1, 4]). */
function bookAfterUpdate(): GexBookState {
  const tracker = new GexBookTracker("SPY");
  for (const name of ["keyframe", "update_1"]) {
    const hex = VECTORS.steps.find((step) => step.name === name)!.frame_hex;
    tracker.apply(streamWire.Frame.decode(bytesOf(hex)));
  }
  return tracker.state!;
}

const BOOK = bookAfterUpdate();
// Strikes 595, 600, 602.5, 605, 610. Spot 601.5.
// call  12.5M, 237.5M, 187.5M, 62.5M, 31.25M
// put  -125M, -93.75M, -46.875M, -15.625M, -3.125M
// gex  -112.5M, 143.75M, 140.625M, 46.875M, 28.125M

const strikes = (view: GexBookState): number[] => Array.from(view.strikes);

describe("filters", () => {
  it("all() returns the book unchanged", () => {
    expect(all()(BOOK)).toBe(BOOK);
  });

  it("keeps the header and aligns every column", () => {
    const view = between(600, 605)(BOOK);
    for (const key of [
      "symbol", "capturedAt", "bookId", "version", "spot", "netGex", "flipPoint", "reason",
    ] as const) {
      expect(view[key]).toBe(BOOK[key]);
    }
    expect(strikes(view)).toEqual([600, 602.5, 605]);
    expect(Array.from(view.strikesThousandths)).toEqual([600_000, 602_500, 605_000]);
    expect(Array.from(view.callGex)).toEqual([237_500_000, 187_500_000, 62_500_000]);
    expect(Array.from(view.putGex)).toEqual([-93_750_000, -46_875_000, -15_625_000]);
    expect(Array.from(view.gex)).toEqual([143_750_000, 140_625_000, 46_875_000]);
    expect(view.callGex).toBeInstanceOf(Float32Array);
    expect(view.gex).toBeInstanceOf(Float64Array);
    // update_1 changed book positions 1 and 4; 600 is position 0 of the view.
    expect(Array.from(view.changed)).toEqual([0]);
    expect(Object.isFrozen(view)).toBe(true);
  });

  it("does not modify the book", () => {
    const before = strikes(BOOK);
    top(1)(BOOK);
    nearest(2)(BOOK);
    expect(strikes(BOOK)).toEqual(before);
    expect(Array.from(BOOK.changed)).toEqual([1, 4]);
  });

  it("nearest(n) keeps the n strikes closest to spot, in strike order", () => {
    expect(strikes(nearest(1)(BOOK))).toEqual([602.5]); // 1.0 away
    expect(strikes(nearest(3)(BOOK))).toEqual([600, 602.5, 605]);
    expect(strikes(nearest(0)(BOOK))).toEqual([]);
    expect(strikes(nearest(50)(BOOK))).toEqual(strikes(BOOK));
  });

  it("nearest breaks a tie toward the lower strike", () => {
    const tied = { ...BOOK, spot: 601.25 }; // 600 and 602.5 are both 1.25 away
    expect(strikes(nearest(1)(tied))).toEqual([600]);
  });

  it("withinPct(p) keeps |strike / spot - 1| <= p", () => {
    // 595 is 1.08% below 601.5, 605 is 0.58% above, 610 is 1.41% above.
    expect(strikes(withinPct(0.01)(BOOK))).toEqual([600, 602.5, 605]);
    expect(strikes(withinPct(0.015)(BOOK))).toEqual(strikes(BOOK));
    expect(strikes(withinPct(0)(BOOK))).toEqual([]);
    expect(strikes(withinPct(0.05)({ ...BOOK, spot: 0 }))).toEqual([]);
  });

  it("between(lo, hi) includes both ends", () => {
    expect(strikes(between(595, 600)(BOOK))).toEqual([595, 600]);
    expect(strikes(between(601, 602)(BOOK))).toEqual([]);
    expect(strikes(between(602.5, 602.5)(BOOK))).toEqual([602.5]);
  });

  it("top(n, by) ranks by gross, net, call or put and keeps strike order", () => {
    // gross: 331.25M, 234.375M, 218.75M, 78.125M, 34.375M
    expect(strikes(top(2)(BOOK))).toEqual([600, 602.5]);
    expect(strikes(top(2, "gross")(BOOK))).toEqual([600, 602.5]);
    // net |gex|: 112.5M, 143.75M, 140.625M, 46.875M, 28.125M
    expect(strikes(top(3, "net")(BOOK))).toEqual([595, 600, 602.5]);
    expect(strikes(top(1, "call")(BOOK))).toEqual([600]);
    // put ranks by size: the largest put wall first.
    expect(strikes(top(2, "put")(BOOK))).toEqual([595, 600]);
    expect(strikes(top(0)(BOOK))).toEqual([]);
  });

  it("top breaks a tie toward the lower strike", () => {
    const flat = {
      ...BOOK,
      callGex: Float32Array.from([1, 1, 1, 1, 1]),
      putGex: Float32Array.from([0, 0, 0, 0, 0]),
      gex: Float64Array.from([1, 1, 1, 1, 1]),
    };
    expect(strikes(top(2)(flat))).toEqual([595, 600]);
  });

  it("rejects bad arguments when the filter is built", () => {
    expect(() => nearest(-1)).toThrow(RangeError);
    expect(() => nearest(1.5)).toThrow(RangeError);
    expect(() => top(Number.NaN)).toThrow(RangeError);
    expect(() => top(3, "oi" as never)).toThrow(RangeError);
    expect(() => withinPct(-0.01)).toThrow(RangeError);
    expect(() => withinPct(Number.POSITIVE_INFINITY)).toThrow(RangeError);
    expect(() => between(610, 600)).toThrow(RangeError);
  });

  it("builds from the options object of stream.gex", () => {
    expect(buildFilter({})).toBeUndefined();
    expect(() => buildFilter({ nearest: 3, within: 0.1 })).toThrow(RangeError);
    expect(() => buildFilter({ by: "net" })).toThrow(RangeError);
    expect(() => buildFilter({ near: 3 } as never)).toThrow(TypeError);
    expect(() => buildFilter({ between: 600 as never })).toThrow(TypeError);
    expect(() => buildFilter({ within: "5%" as never })).toThrow(TypeError);
  });
});
