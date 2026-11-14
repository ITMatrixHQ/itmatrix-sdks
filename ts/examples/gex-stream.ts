/**
 * Stream a live GEX book for one symbol.
 *
 * Needs an ITMatrixHQ API key and Node 22 or later:
 *
 *     ITM_API_KEY=itm_... node gex-stream.js SPY
 *
 * API Explorer and API Pro keys can stream. One client is one
 * connection, so pass every symbol you need to the same client.
 *
 * The book holds call and put GEX per strike; `gex` is their sum. This example
 * prints the 20 strikes nearest spot. The filter picks among the book's 120
 * strikes, which the server ranks by gross GEX, so the 20 can have gaps.
 *
 * In your own project import from "@itmatrixhq/core". The relative path keeps
 * this file checked against the source in this repository.
 */
import { ITMClient, nearest, StreamError } from "../packages/core/src/index.js";

// The example type-checks without the Node typings; this is the part it uses.
declare const process: {
  env: Record<string, string | undefined>;
  argv: string[];
  exitCode?: number;
  on(event: "SIGINT", listener: () => void): void;
};

async function main(symbol: string): Promise<void> {
  const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
  process.on("SIGINT", () => client.close());
  try {
    const book = client.stream.gexBook(symbol, { filter: nearest(20) });
    for await (const state of book) {
      let top = 0;
      for (let i = 1; i < state.gex.length; i += 1) {
        if (Math.abs(state.gex[i]!) > Math.abs(state.gex[top]!)) top = i;
      }
      const dollars = (value: number): string => Math.round(value).toLocaleString("en-US");
      console.log(
        `${state.symbol} v${state.version} spot=${state.spot.toFixed(2)} ` +
          `net_gex=${dollars(state.netGex)} flip=${state.flipPoint} ` +
          `strikes=${state.strikes.length} changed=${state.changed.length} ` +
          `largest=${state.strikes[top]} (call ${dollars(state.callGex[top]!)}, ` +
          `put ${dollars(state.putGex[top]!)}, total ${dollars(state.gex[top]!)})`,
      );
    }
  } finally {
    client.close();
  }
}

main(process.argv[2] ?? "SPY").catch((error: unknown) => {
  if (!(error instanceof StreamError)) throw error;
  // `reason` names a refusal such as client_outdated or connection_limit.
  console.error(`${error.code} ${error.reason ?? ""}: ${error.message}`);
  process.exitCode = 1;
});
