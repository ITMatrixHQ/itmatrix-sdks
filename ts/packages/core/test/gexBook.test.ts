/**
 * GEX book v2 reconstruction against the backend's golden vectors.
 *
 * `fixtures/gex_strike_book_vectors.json` is a verbatim copy of the backend's
 * `contract/sdk/fixtures/gex_strike_book_vectors.json`. The numbers are
 * synthetic. Every other test builds on these bytes.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { streamWire } from "../src/index.js";
import { bookCrc, crc32, GexBookTracker, type GexBookState } from "../src/gexBook.js";

const { Frame, GexBookReason } = streamWire;
type Frame = streamWire.Frame;

interface VectorBook {
  book_id: string;
  version: number;
  captured_at_ms: number;
  spot: number;
  net_gex: number;
  flip_point: number | null;
  strikes: number[];
  call_gex: number[];
  put_gex: number[];
  gex: number[];
  crc: number;
}
interface Step {
  name: string;
  frame_hex: string;
  frame: { symbol: string; gex_strike_book?: Record<string, unknown>; gex_strike_book_update?: Record<string, unknown> };
  expect: "apply" | "resync";
  crc_after_applying?: number;
  state: VectorBook;
}
const VECTORS = JSON.parse(
  readFileSync(new URL("./fixtures/gex_strike_book_vectors.json", import.meta.url), "utf8"),
) as { symbol: string; steps: Step[] };
const step = (name: string): Step => VECTORS.steps.find((value) => value.name === name)!;

const KEYFRAME = step("keyframe");
const UPDATE_1 = step("update_1");
const UPDATE_2 = step("update_2");
const MISMATCH = step("update_crc_mismatch");
const BOOK_ID = BigInt(KEYFRAME.state.book_id);

const bytesOf = (hex: string): Uint8Array =>
  Uint8Array.from(hex.match(/../g) ?? [], (pair) => parseInt(pair, 16));
const hexOf = (bytes: Uint8Array): string =>
  Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
const frame = (value: Step): Frame => Frame.decode(bytesOf(value.frame_hex));

/** The vector keyframe at another version, its CRC unchanged (the book is the same). */
function keyframeAt(version: number, bookId = BOOK_ID): Frame {
  const value = frame(KEYFRAME);
  value.gexStrikeBook!.version = version;
  value.gexStrikeBook!.bookId = bookId;
  return value;
}

/** The client state in the fixture's shape. */
function asVector(state: GexBookState): VectorBook {
  return {
    book_id: state.bookId.toString(),
    version: state.version,
    captured_at_ms: state.capturedAt.getTime(),
    spot: state.spot,
    net_gex: state.netGex,
    flip_point: state.flipPoint,
    strikes: Array.from(state.strikesThousandths),
    call_gex: Array.from(state.callGex),
    put_gex: Array.from(state.putGex),
    gex: Array.from(state.gex),
    crc: bookCrc(state.strikesThousandths, state.callGex, state.putGex),
  };
}

describe("golden vectors", () => {
  it("is the backend fixture for SPY with four steps", () => {
    expect(VECTORS.symbol).toBe("SPY");
    expect(VECTORS.steps.map((value) => [value.name, value.expect])).toEqual([
      ["keyframe", "apply"],
      ["update_1", "apply"],
      ["update_2", "apply"],
      ["update_crc_mismatch", "resync"],
    ]);
  });

  it("applies every step in order to one state, as the fixture says", () => {
    const book = new GexBookTracker(VECTORS.symbol);
    for (const value of VECTORS.steps) {
      const before = book.state;
      const { state, resync } = book.apply(frame(value));
      if (value.expect === "apply") {
        expect(resync, value.name).toBe(false);
        expect(asVector(state!), value.name).toEqual(value.state);
      } else {
        expect([state, resync], value.name).toEqual([undefined, true]);
        expect(book.state, value.name).toBe(before);
        expect(asVector(book.state!), value.name).toEqual(value.state);
      }
    }
  });

  it("decodes the keyframe as field 23 and re-encodes it byte for byte", () => {
    const value = frame(KEYFRAME);
    expect(value.symbol).toBe("SPY");
    expect(value.gexStrikeBookUpdate).toBeUndefined();
    const book = value.gexStrikeBook!;
    const wire = KEYFRAME.frame.gex_strike_book!;
    expect(book.capturedAtMs).toBe(BigInt(wire.captured_at_ms as number));
    expect(book.bookId).toBe(BOOK_ID);
    expect(book.version).toBe(7);
    expect(book.spot).toBe(601.25);
    expect(book.netGex).toBe(-1_500_000_000);
    expect(book.flipPoint).toBe(598.5);
    expect(book.strikes).toEqual((wire.strikes_delta as number[]).map(BigInt));
    expect(book.callGex).toEqual(wire.call_gex);
    expect(book.putGex).toEqual(wire.put_gex);
    expect(book.crc).toBe(wire.crc);
    expect(book.reason).toBe(GexBookReason.GEX_BOOK_REASON_SUBSCRIBE);
    expect(hexOf(Frame.encode(value).finish())).toBe(KEYFRAME.frame_hex);
  });

  it("decodes the updates as field 24 and re-encodes them byte for byte", () => {
    for (const value of [UPDATE_1, UPDATE_2, MISMATCH]) {
      const decoded = frame(value);
      expect(decoded.gexStrikeBook).toBeUndefined();
      const update = decoded.gexStrikeBookUpdate!;
      const wire = value.frame.gex_strike_book_update!;
      expect(update.bookId).toBe(BOOK_ID);
      expect(update.version).toBe(wire.version);
      expect(update.spot).toBe(wire.spot);
      expect(update.netGex).toBe(wire.net_gex);
      expect(update.flipPoint ?? null).toBe(wire.flip_point);
      expect(update.index).toEqual(wire.index);
      expect(update.callGex).toEqual(wire.call_gex);
      expect(update.putGex).toEqual(wire.put_gex);
      expect(update.crc).toBe(wire.crc);
      expect(hexOf(Frame.encode(decoded).finish())).toBe(value.frame_hex);
    }
  });

  it("has the published CRC at every step, and the mismatch step is off by its frame", () => {
    for (const value of [KEYFRAME, UPDATE_1, UPDATE_2]) {
      const s = value.state;
      expect(bookCrc(s.strikes, s.call_gex, s.put_gex), value.name).toBe(s.crc);
    }
    const s = UPDATE_2.state;
    const call = Float32Array.from(s.call_gex);
    const put = Float32Array.from(s.put_gex);
    call[3] = 64_000_000;
    put[3] = -15_500_000;
    expect(bookCrc(s.strikes, call, put)).toBe(MISMATCH.crc_after_applying);
    expect(MISMATCH.frame.gex_strike_book_update!.crc).not.toBe(MISMATCH.crc_after_applying);
  });

  it("computes CRC-32/IEEE over strike, call bits, put bits, little-endian", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf4_3926);
    expect(bookCrc([], [], [])).toBe(0);
    const s = KEYFRAME.state;
    const raw = new Uint8Array(s.strikes.length * 16);
    const view = new DataView(raw.buffer);
    s.strikes.forEach((strike, i) => {
      view.setBigInt64(i * 16, BigInt(strike), true);
      view.setUint32(i * 16 + 8, new Uint32Array(Float32Array.of(s.call_gex[i]!).buffer)[0]!, true);
      view.setUint32(i * 16 + 12, new Uint32Array(Float32Array.of(s.put_gex[i]!).buffer)[0]!, true);
    });
    expect(crc32(raw)).toBe(s.crc);
  });
});

describe("GexBookTracker", () => {
  const keyframe = (book: GexBookTracker): void => {
    const { state, resync } = book.apply(frame(KEYFRAME));
    expect(state).toBeDefined();
    expect(resync).toBe(false);
  };
  const nothing = { state: undefined, resync: false };
  const dropped = { state: undefined, resync: true };

  it("replaces state with a keyframe", () => {
    const book = new GexBookTracker("SPY");
    const { state, resync } = book.apply(frame(KEYFRAME));
    expect(resync).toBe(false);
    expect(state!.symbol).toBe("SPY");
    expect(state!.bookId).toBe(BOOK_ID);
    expect(state!.version).toBe(7);
    expect(state!.flipPoint).toBe(598.5);
    expect(Array.from(state!.strikes)).toEqual([595, 600, 602.5, 605, 610]);
    expect(Array.from(state!.changed)).toEqual([0, 1, 2, 3, 4]);
    expect(state!.reason).toBe("subscribe");
    expect(state!.capturedAt.getTime()).toBe(1_761_998_400_000);
    expect(state!.callGex).toBeInstanceOf(Float32Array);
    expect(state!.putGex).toBeInstanceOf(Float32Array);
    expect(state!.gex).toBeInstanceOf(Float64Array);
    expect(state!.strikes).toBeInstanceOf(Float64Array);
    expect(Object.keys(state!).filter((key) => /Oi$|openInterest/i.test(key))).toEqual([]);
  });

  it("derives gex as call plus put in double precision", () => {
    const book = new GexBookTracker("SPY");
    const value = frame(KEYFRAME);
    // Two float32 values whose float32 sum would round.
    value.gexStrikeBook!.callGex[0] = 33_554_432; // 2^25, exact in float32
    value.gexStrikeBook!.putGex[0] = -1;
    const call = Float32Array.from(value.gexStrikeBook!.callGex);
    const put = Float32Array.from(value.gexStrikeBook!.putGex);
    value.gexStrikeBook!.crc = bookCrc(KEYFRAME.state.strikes, call, put);
    const { state } = book.apply(value);
    expect(state!.gex[0]).toBe(call[0]! + put[0]!);
    expect(state!.gex[0]).toBe(33_554_431);
    expect(Math.fround(state!.gex[0]!)).not.toBe(state!.gex[0]);
  });

  it("applies updates and records the changed indices", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    const one = book.apply(frame(UPDATE_1)).state!;
    expect([one.version, Array.from(one.changed), one.reason]).toEqual([8, [1, 4], null]);
    const two = book.apply(frame(UPDATE_2)).state!;
    expect([two.version, Array.from(two.changed), two.flipPoint]).toEqual([9, [0, 2], null]);
  });

  it("bumps the version on an empty update", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    const empty = frame(UPDATE_1);
    const update = empty.gexStrikeBookUpdate!;
    update.index = [];
    update.callGex = [];
    update.putGex = [];
    update.crc = KEYFRAME.state.crc;
    const { state, resync } = book.apply(empty);
    expect(resync).toBe(false);
    expect(state!.version).toBe(8);
    expect(Array.from(state!.changed)).toEqual([]);
  });

  it("ignores a duplicate without a resync", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    book.apply(frame(UPDATE_1));
    expect(book.apply(frame(UPDATE_1))).toEqual(nothing);
    const older = frame(UPDATE_1);
    older.gexStrikeBookUpdate!.version = 2;
    expect(book.apply(older)).toEqual(nothing);
    expect(book.awaitingKeyframe).toBe(false);
  });

  it("resyncs on a gap and drops updates until a keyframe", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    // Version 9 arrives while the book is at 7: version 8 was lost.
    expect(book.apply(frame(UPDATE_2))).toEqual(dropped);
    expect(book.awaitingKeyframe).toBe(true);
    expect(book.apply(frame(UPDATE_1))).toEqual(dropped);
    const { state, resync } = book.apply(keyframeAt(9));
    expect(resync).toBe(false);
    expect(state!.version).toBe(9);
    expect(book.awaitingKeyframe).toBe(false);
  });

  it("leaves the book untouched and resyncs on an update CRC mismatch", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    book.apply(frame(UPDATE_1));
    book.apply(frame(UPDATE_2));
    const before = book.state;
    expect(book.apply(frame(MISMATCH))).toEqual(dropped);
    expect(book.state).toBe(before);
    // The same frame with the right CRC is still dropped until a keyframe.
    const fixed = frame(MISMATCH);
    fixed.gexStrikeBookUpdate!.crc = MISMATCH.crc_after_applying!;
    expect(book.apply(fixed)).toEqual(dropped);
  });

  it("applies the mismatch frame once its CRC is right", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    book.apply(frame(UPDATE_1));
    book.apply(frame(UPDATE_2));
    const fixed = frame(MISMATCH);
    fixed.gexStrikeBookUpdate!.crc = MISMATCH.crc_after_applying!;
    const { state } = book.apply(fixed);
    expect(state!.version).toBe(10);
    expect(state!.callGex[3]).toBe(64_000_000);
    expect(state!.putGex[3]).toBe(-15_500_000);
    expect(state!.gex[3]).toBe(48_500_000);
  });

  it("resyncs on an update for another book", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    const other = frame(UPDATE_1);
    other.gexStrikeBookUpdate!.bookId = BOOK_ID + 1n;
    expect(book.apply(other)).toEqual(dropped);
  });

  it("adopts a new book id and version from a keyframe, whatever came before", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    book.apply(frame(UPDATE_1));
    const { state, resync } = book.apply(keyframeAt(1, BOOK_ID + 7n));
    expect(resync).toBe(false);
    expect([state!.bookId, state!.version]).toEqual([BOOK_ID + 7n, 1]);
    expect(Array.from(state!.gex)).toEqual(KEYFRAME.state.gex);
  });

  it("drops an update that arrives before any keyframe, and after a reset", () => {
    const book = new GexBookTracker("SPY");
    expect(book.apply(frame(UPDATE_1))).toEqual(dropped);
    keyframe(book);
    book.reset();
    expect(book.state).toBeUndefined();
    expect(book.apply(frame(UPDATE_1))).toEqual(dropped);
  });

  it("resyncs on a malformed update", () => {
    const book = new GexBookTracker("SPY");
    const cases: Array<(u: streamWire.GexStrikeBookUpdate) => void> = [
      (u) => { u.index[1] = 9; },
      (u) => { u.callGex.pop(); },
      (u) => { u.putGex.pop(); },
      (u) => { u.index = [4, 1]; },
      (u) => { u.index = [1, 1]; },
    ];
    for (const mutate of cases) {
      book.apply(keyframeAt(7));
      const bad = frame(UPDATE_1);
      mutate(bad.gexStrikeBookUpdate!);
      expect(book.apply(bad)).toEqual(dropped);
    }
  });

  it("does not adopt a malformed or corrupted keyframe", () => {
    const book = new GexBookTracker("SPY");
    const mutations: Array<(b: streamWire.GexStrikeBook) => void> = [
      (b) => { b.callGex.shift(); },
      (b) => { b.putGex.pop(); },
      (b) => { b.strikes = [595_000n, 0n, 2_500n, 2_500n, 5_000n]; },
      (b) => { b.crc = (b.crc ^ 1) >>> 0; },
      (b) => { b.callGex[2] = 187_500_016; },
      (b) => { b.putGex[0] = -125_000_008; },
      (b) => { b.strikes[3] = 2_501n; },
    ];
    for (const mutate of mutations) {
      const bad = frame(KEYFRAME);
      mutate(bad.gexStrikeBook!);
      expect(book.apply(bad)).toEqual(dropped);
      expect(book.state).toBeUndefined();
    }
    keyframe(book);
    expect(book.awaitingKeyframe).toBe(false);
  });

  it("keeps the previous book after a corrupted keyframe, but drops updates", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    const before = book.state;
    const bad = frame(KEYFRAME);
    bad.gexStrikeBook!.crc = 0;
    expect(book.apply(bad)).toEqual(dropped);
    expect(book.state).toBe(before);
    expect(book.apply(frame(UPDATE_1))).toEqual(dropped);
  });

  it("gives each state its own arrays", () => {
    const book = new GexBookTracker("SPY");
    keyframe(book);
    const first = book.state!;
    first.callGex[0] = 0;
    first.gex[0] = 0;
    first.strikesThousandths[0] = 1;
    book.apply(frame(UPDATE_1));
    expect(book.state!.callGex[0]).toBe(12_500_000);
    expect(book.state!.gex[0]).toBe(-112_500_000);
    expect(book.state!.strikesThousandths[0]).toBe(595_000);
    expect(Object.isFrozen(book.state)).toBe(true);
  });

  it("ignores a frame without a book", () => {
    const book = new GexBookTracker("SPY");
    expect(book.apply({ symbol: "SPY" })).toEqual(nothing);
    expect(book.apply(Frame.decode(Frame.encode(Frame.fromPartial({ symbol: "SPY" })).finish()))).toEqual(nothing);
  });
});
