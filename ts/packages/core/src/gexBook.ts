/**
 * Client-side GEX book: keyframes, updates and the CRC that ties them together.
 *
 * The stream sends one `GexStrikeBook` keyframe per subscription and then
 * `GexStrikeBookUpdate` messages that carry only the strikes whose call or put
 * GEX changed. {@link GexBookTracker} holds the book for one symbol and applies
 * those frames. It does no I/O, so every rule here can be tested with bytes
 * alone.
 *
 * Rules:
 *
 * - A keyframe replaces all state and adopts its `bookId` and `version`, once
 *   its CRC checks out.
 * - An update applies only when `bookId` matches and `version` is exactly one
 *   more than the current version, and only if the CRC of the book after
 *   applying it matches the CRC in the message.
 * - A `version` at or below the current one is a duplicate and is ignored.
 * - Anything else, or a CRC mismatch, leaves the book untouched and asks for a
 *   resync. Updates are dropped until the next keyframe arrives.
 *
 * The v1 book messages (`GexBook`, `GexBookUpdate`, Frame fields 21 and 22) are
 * never emitted and are not decoded here.
 *
 * Only types are imported from the generated bindings, so this module does not
 * pull the protobuf codec into a bundle that never subscribes.
 */
import type { Frame, GexStrikeBook, GexStrikeBookUpdate } from "./_wire/ws.js";

/** Why the server sent a keyframe. */
export type GexBookReasonName =
  "subscribe" | "periodic" | "resync" | "rebuild" | "recovery";

/** `GexBookReason` numbers on the wire. Unspecified or unknown reads as `null`. */
const REASONS = new Map<number, GexBookReasonName>([
  [1, "subscribe"],
  [2, "periodic"],
  [3, "resync"],
  [4, "rebuild"],
  [5, "recovery"],
]);

/**
 * The book after one applied keyframe or update.
 *
 * `strikes` are dollars and `strikesThousandths` the same strikes as exact
 * integers, both ascending. `callGex` (>= 0) and `putGex` (<= 0) are dollars
 * of dealer hedging per $1 move as single-precision floats, aligned with the
 * strikes. `gex` is their sum per strike, computed in double precision; the
 * server does not send it. `netGex` is the whole-chain total, not the sum of
 * the listed strikes. `changed` lists the indices the latest frame updated
 * (every index for a keyframe), and `reason` says why a keyframe was sent
 * (`null` for an update).
 *
 * Every state owns its arrays (views over one buffer per state) and the
 * object is frozen, so keep or mutate a state freely: it cannot corrupt the
 * book. Treat the arrays as read-only.
 */
export interface GexBookState {
  readonly symbol: string;
  readonly capturedAt: Date;
  readonly bookId: bigint;
  readonly version: number;
  readonly spot: number;
  readonly netGex: number;
  readonly flipPoint: number | null;
  readonly strikes: Float64Array;
  readonly strikesThousandths: Float64Array;
  readonly callGex: Float32Array;
  readonly putGex: Float32Array;
  readonly gex: Float64Array;
  readonly changed: Uint32Array;
  readonly reason: GexBookReasonName | null;
}

/** What one frame did: the new book when it changed, and whether to resync. */
export interface GexBookApplied {
  /** The book after the frame, or `undefined` when the frame did not change it. */
  readonly state: GexBookState | undefined;
  /**
   * True when the client must ask for a fresh keyframe. It stays true for every
   * update dropped while waiting for one.
   */
  readonly resync: boolean;
}

/** Strikes beyond this many thousandths cannot be held exactly in a number. */
const MAX_STRIKE = BigInt(Number.MAX_SAFE_INTEGER);

/** Slice-by-8 CRC-32/IEEE tables: `CRC_TABLES[k * 256 + b]`. */
const CRC_TABLES = (() => {
  const tables = new Uint32Array(8 * 256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    tables[n] = c >>> 0;
  }
  for (let n = 0; n < 256; n += 1) {
    let c = tables[n]!;
    for (let k = 1; k < 8; k += 1) {
      c = tables[c & 0xff]! ^ (c >>> 8);
      tables[k * 256 + n] = c >>> 0;
    }
  }
  return tables;
})();

/** CRC-32/IEEE (the zlib checksum) of a byte string, as an unsigned integer. */
export function crc32(bytes: Uint8Array): number {
  const t = CRC_TABLES;
  const length = bytes.length;
  let crc = 0xffffffff;
  let i = 0;
  for (const end = length - (length & 7); i < end; i += 8) {
    const lo =
      crc ^ (bytes[i]! | (bytes[i + 1]! << 8) | (bytes[i + 2]! << 16) | (bytes[i + 3]! << 24));
    crc =
      t[1792 + (lo & 0xff)]! ^
      t[1536 + ((lo >>> 8) & 0xff)]! ^
      t[1280 + ((lo >>> 16) & 0xff)]! ^
      t[1024 + (lo >>> 24)]! ^
      t[768 + bytes[i + 4]!]! ^
      t[512 + bytes[i + 5]!]! ^
      t[256 + bytes[i + 6]!]! ^
      t[bytes[i + 7]!]!;
  }
  for (; i < length; i += 1) crc = t[(crc ^ bytes[i]!) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** Bytes per strike in the CRC input: int64 strike, float32 call, float32 put. */
const ROW = 16;
const TWO_32 = 4294967296;

/** The CRC input of a book: one 16-byte little-endian row per strike. */
function crcRows(
  strikesThousandths: ArrayLike<number>,
  callGex: ArrayLike<number>,
  putGex: ArrayLike<number>,
): Uint8Array {
  const count = strikesThousandths.length;
  const bytes = new Uint8Array(count * ROW);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < count; i += 1) {
    // An exact integer below 2^53 split into int64 halves, without a BigInt.
    const strike = strikesThousandths[i]!;
    const high = Math.floor(strike / TWO_32);
    view.setUint32(i * ROW, strike - high * TWO_32, true);
    view.setInt32(i * ROW + 4, high, true);
    view.setFloat32(i * ROW + 8, callGex[i]!, true);
    view.setFloat32(i * ROW + 12, putGex[i]!, true);
  }
  return bytes;
}

/**
 * CRC-32 (IEEE, as zlib) of a book.
 *
 * For each strike in ascending order: the strike in thousandths as a
 * little-endian int64, then the call GEX as little-endian float32 bits, then
 * the put GEX the same way.
 */
export function bookCrc(
  strikesThousandths: ArrayLike<number>,
  callGex: ArrayLike<number>,
  putGex: ArrayLike<number>,
): number {
  return crc32(crcRows(strikesThousandths, callGex, putGex));
}

export class GexBookTracker {
  /** The latest applied book, or `undefined` before the first keyframe. */
  state: GexBookState | undefined;
  /** True after a desync: updates are dropped until a keyframe arrives. */
  awaitingKeyframe = false;
  private thousandths = new Float64Array(0);
  private call = new Float32Array(0);
  private put = new Float32Array(0);
  /** The strikes in dollars, computed once per keyframe. */
  private dollars = new Float64Array(0);
  /** The CRC input of the current book, patched in place of a full rebuild. */
  private rows: Uint8Array = new Uint8Array(0);

  constructor(readonly symbol: string) {}

  /** Forget the book, as after a reconnect. Updates wait for a keyframe. */
  reset(): void {
    this.state = undefined;
    this.awaitingKeyframe = false;
    this.thousandths = new Float64Array(0);
    this.call = new Float32Array(0);
    this.put = new Float32Array(0);
    this.dollars = new Float64Array(0);
    this.rows = new Uint8Array(0);
  }

  apply(frame: Frame): GexBookApplied {
    if (frame.gexStrikeBookUpdate !== undefined) {
      return this.update(frame.gexStrikeBookUpdate);
    }
    if (frame.gexStrikeBook !== undefined) return this.keyframe(frame.gexStrikeBook);
    return { state: undefined, resync: false };
  }

  private keyframe(book: GexStrikeBook): GexBookApplied {
    const count = book.strikes.length;
    if (book.callGex.length !== count || book.putGex.length !== count) {
      // A malformed keyframe cannot be adopted. Ask for another.
      return this.desync();
    }
    const thousandths = new Float64Array(count);
    let running = 0n;
    for (let i = 0; i < count; i += 1) {
      running += book.strikes[i]!;
      if (running > MAX_STRIKE || running < -MAX_STRIKE) return this.desync();
      thousandths[i] = Number(running);
      if (i > 0 && thousandths[i]! <= thousandths[i - 1]!) return this.desync();
    }
    const call = Float32Array.from(book.callGex);
    const put = Float32Array.from(book.putGex);
    const rows = crcRows(thousandths, call, put);
    if (crc32(rows) !== book.crc >>> 0) return this.desync();
    this.thousandths = thousandths;
    this.call = call;
    this.put = put;
    this.rows = rows;
    const dollars = new Float64Array(count);
    for (let i = 0; i < count; i += 1) dollars[i] = thousandths[i]! / 1000;
    this.dollars = dollars;
    this.awaitingKeyframe = false;
    this.state = this.snapshot(
      book.capturedAtMs,
      book.bookId,
      book.version,
      book.spot,
      book.netGex,
      book.flipPoint,
      undefined,
      REASONS.get(book.reason) ?? null,
    );
    return { state: this.state, resync: false };
  }

  private update(update: GexStrikeBookUpdate): GexBookApplied {
    const state = this.state;
    if (state === undefined || this.awaitingKeyframe) {
      // Nothing to apply it to, or already resyncing: drop it.
      return { state: undefined, resync: true };
    }
    if (update.bookId !== state.bookId) return this.desync();
    if (update.version <= state.version) {
      return { state: undefined, resync: false }; // duplicate
    }
    if (update.version !== state.version + 1) return this.desync();
    const index = update.index;
    const callGex = update.callGex;
    const putGex = update.putGex;
    const count = this.thousandths.length;
    const changes = index.length;
    if (callGex.length !== changes || putGex.length !== changes) return this.desync();
    let previous = -1;
    for (let i = 0; i < changes; i += 1) {
      const position = index[i]!;
      if (position <= previous || position >= count) return this.desync();
      previous = position;
    }
    // Patch the CRC rows in place: 8 value bytes per changed strike. On a
    // mismatch the rows are stale, which is harmless: the tracker then waits
    // for a keyframe, which replaces them. The value arrays change only once
    // the CRC agrees.
    const rows = this.rows;
    const view = new DataView(rows.buffer, rows.byteOffset, rows.byteLength);
    for (let i = 0; i < changes; i += 1) {
      const at = index[i]! * ROW;
      view.setFloat32(at + 8, callGex[i]!, true);
      view.setFloat32(at + 12, putGex[i]!, true);
    }
    if (crc32(rows) !== update.crc >>> 0) return this.desync();
    // Every state holds copies, so the tracker's arrays can change in place.
    const call = this.call;
    const put = this.put;
    for (let i = 0; i < changes; i += 1) {
      call[index[i]!] = callGex[i]!;
      put[index[i]!] = putGex[i]!;
    }
    this.state = this.snapshot(
      update.capturedAtMs,
      state.bookId,
      update.version,
      update.spot,
      update.netGex,
      update.flipPoint,
      index,
      null,
    );
    return { state: this.state, resync: false };
  }

  private desync(): GexBookApplied {
    this.awaitingKeyframe = true;
    return { state: undefined, resync: true };
  }

  /**
   * A state that owns copies of the book. All of its arrays are views over one
   * buffer: one allocation per state instead of six.
   */
  private snapshot(
    capturedAtMs: bigint,
    bookId: bigint,
    version: number,
    spot: number,
    netGex: number,
    flipPoint: number | undefined,
    changedIndex: ArrayLike<number> | undefined,
    reason: GexBookReasonName | null,
  ): GexBookState {
    const call = this.call;
    const put = this.put;
    const count = call.length;
    const changes = changedIndex === undefined ? count : changedIndex.length;
    const buffer = new ArrayBuffer(32 * count + 4 * changes);
    const strikes = new Float64Array(buffer, 0, count);
    strikes.set(this.dollars);
    const strikesThousandths = new Float64Array(buffer, 8 * count, count);
    strikesThousandths.set(this.thousandths);
    const gex = new Float64Array(buffer, 16 * count, count);
    for (let i = 0; i < count; i += 1) gex[i] = call[i]! + put[i]!;
    const callGex = new Float32Array(buffer, 24 * count, count);
    callGex.set(call);
    const putGex = new Float32Array(buffer, 28 * count, count);
    putGex.set(put);
    const changed = new Uint32Array(buffer, 32 * count, changes);
    if (changedIndex === undefined) {
      for (let i = 0; i < count; i += 1) changed[i] = i;
    } else {
      changed.set(changedIndex);
    }
    return Object.freeze({
      symbol: this.symbol,
      capturedAt: new Date(Number(capturedAtMs)),
      bookId,
      version,
      spot,
      netGex,
      flipPoint: flipPoint ?? null,
      strikes,
      strikesThousandths,
      callGex,
      putGex,
      gex,
      changed,
      reason,
    });
  }
}
