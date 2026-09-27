/**
 * Feeding `getBars(...).data` into the ITMScript runtime, headless, in Node.
 *
 * The prose version is `docs/itmscript.md`. This file exists so the shape
 * claimed there is checked by the gate rather than trusted.
 *
 * It depends on nothing but this repository: the runtime is not published, so
 * its surface is declared locally, exactly as `docs/itmscript.md` records it.
 * Swap the declaration for a real import once the package ships.
 */
import type { Bar } from "../packages/core/src/types.js";

// ---------------------------------------------------------------------------
// The runtime's surface, as of the prototype. Declared, not imported: see above.
// ---------------------------------------------------------------------------
interface Plot {
  label: string;
  color: string;
  width: number;
  style: "line" | "histogram";
  pane: "overlay" | "separate";
  points: Array<{ timeMs: number; value: number | null }>;
}
interface StudyResult {
  version: 1;
  title: string;
  plots: Plot[];
}
interface Study {
  snapshot(): StudyResult;
  reset(bars: ITMScriptBar[]): StudyResult;
  append(bar: ITMScriptBar): StudyResult;
  update(bar: ITMScriptBar): StudyResult;
}
declare function run(source: string, bars: ITMScriptBar[]): StudyResult;
declare function createStudy(source: string, bars?: ITMScriptBar[]): Study;

/**
 * What the runtime accepts. It is the public `Bar` narrowed to the fields it
 * reads — a structural subset, so a `Bar` is assignable to it and the optional
 * `vwap`/`trade_count` are simply ignored.
 */
type ITMScriptBar = Pick<
  Bar, "symbol" | "timeframe" | "ts_ms" | "open" | "high" | "low" | "close" | "volume"
>;

// The assignment the whole cookbook rests on: no adapter, no mapping step.
const assignable: (bars: Bar[]) => ITMScriptBar[] = (bars) => bars;

/**
 * One symbol, one timeframe, ascending by `ts_ms`, no gaps invented.
 *
 * The runtime rejects a mixed array rather than guessing, so the host does the
 * rejecting first, where it can say which page was wrong.
 */
export function assertRunnable(bars: Bar[]): ITMScriptBar[] {
  if (bars.length === 0) throw new Error("no bars: the study has nothing to compute over");
  const first = bars[0]!;
  for (const [index, bar] of bars.entries()) {
    if (bar.symbol !== first.symbol || bar.timeframe !== first.timeframe) {
      throw new Error(
        `bar ${index} is ${bar.symbol}/${bar.timeframe}, not ${first.symbol}/${first.timeframe}`,
      );
    }
    if (index > 0 && bar.ts_ms <= bars[index - 1]!.ts_ms) {
      throw new Error(`bar ${index} is not after bar ${index - 1} (ts_ms ${bar.ts_ms})`);
    }
  }
  return assignable(bars);
}

/**
 * Drain the cursor before running: a study over one page is a study over a
 * truncated history, and it will read as a real number rather than a partial one.
 */
export async function loadHistory(
  client: {
    getBars(symbol: string, options: {
      from: number; to: number; timeframe: Bar["timeframe"]; limit?: number; cursor?: string;
    }): Promise<{ data: Bar[]; meta: { cursor?: string | null } }>;
  },
  symbol: string,
  window: { from: number; to: number; timeframe: Bar["timeframe"] },
  maxPages = 20,
): Promise<Bar[]> {
  const bars: Bar[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  for (let page = 0; page < maxPages; page += 1) {
    const result: { data: Bar[]; meta: { cursor?: string | null } } = await client.getBars(
      symbol,
      cursor === undefined ? { ...window } : { ...window, cursor },
    );
    bars.push(...result.data);
    const next = result.meta.cursor ?? undefined;
    if (!next) return bars;
    if (seen.has(next)) throw new Error("backend repeated a pagination cursor");
    seen.add(next);
    cursor = next;
  }
  throw new Error(`history did not terminate within ${maxPages} pages`);
}

export function trendBaseline(bars: Bar[]): StudyResult {
  return run(
    [
      'study "Trend baseline"',
      "let baseline be the 20 bar average of close",
      'show baseline as "Baseline" with color blue and width 2',
    ].join("\n"),
    assertRunnable(bars),
  );
}

/**
 * Live: the same plan kept incrementally. `append` for a new bucket, `update`
 * for a correction to the newest one — never `append` for both, or the study
 * grows a bar the market never printed.
 */
export function liveStudy(history: Bar[]): Study {
  return createStudy("show the 20 bar average of close", assertRunnable(history));
}
