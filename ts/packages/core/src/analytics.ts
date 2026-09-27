/** Deterministic client-side analytics over public ITMatrixHQ models. */

import type { GexGrid } from "./types.js";

export interface GexLevel {
  strike: number;
  strikeThousandths: number;
  gex: number;
  distanceFromSpot: number | null;
}

export interface GexAnalysis {
  /** Original grid and its server-computed full-curve scalars. */
  grid: GexGrid;
  netGex: number;
  regime: "positive" | "negative" | "flat";
  /** The backend's null-honest flip point, never recomputed from trimmed rows. */
  zeroGamma: number | null;
  positiveLevels: GexLevel[];
  negativeLevels: GexLevel[];
  /** Rankings are among returned rows; tier limits or `top` may hide others. */
  levelsScope: "returned_rows";
}

export function analyzeGex(grid: GexGrid, options: { levels?: number } = {}): GexAnalysis {
  const count = options.levels ?? 3;
  if (!Number.isInteger(count) || count < 1) throw new RangeError("levels must be at least 1");
  if (!Number.isFinite(grid.net_gex)) throw new RangeError("net_gex must be finite");
  const byStrike = new Map<number, number>();
  for (const row of grid.strikes) {
    if (!Number.isSafeInteger(row.strike) || !Number.isFinite(row.gex)) {
      throw new RangeError("strike and gex must be finite safe numbers");
    }
    byStrike.set(row.strike, (byStrike.get(row.strike) ?? 0) + row.gex);
  }
  const asLevel = ([strikeThousandths, gex]: [number, number]): GexLevel => {
    const strike = strikeThousandths / 1_000;
    return { strike, strikeThousandths, gex,
      distanceFromSpot: grid.spot === null ? null : strike - grid.spot };
  };
  const rows = [...byStrike.entries()];
  const positiveLevels = rows.filter(([, gex]) => gex > 0)
    .sort((a, b) => b[1] - a[1] || a[0] - b[0]).slice(0, count).map(asLevel);
  const negativeLevels = rows.filter(([, gex]) => gex < 0)
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]).slice(0, count).map(asLevel);
  return {
    grid, netGex: grid.net_gex,
    regime: grid.net_gex > 0 ? "positive" : grid.net_gex < 0 ? "negative" : "flat",
    zeroGamma: grid.flip_point, positiveLevels, negativeLevels,
    levelsScope: "returned_rows",
  };
}
