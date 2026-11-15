/**
 * Print SPY's live GEX book every time it changes.
 *
 *     ITM_API_KEY=itm_... node gex-stream.js
 *
 * Ctrl-C closes the client, which closes the book, and `run()` resolves.
 * In your own project import from "@itmatrixhq/core". The relative path keeps
 * this file checked against the source in this repository.
 */
import { ITMClient } from "../packages/core/src/index.js";

declare const process: { env: Record<string, string | undefined>; on(e: "SIGINT", f: () => void): void };

const client = new ITMClient({ apiKey: process.env.ITM_API_KEY });
const spy = client.stream.gex("SPY", { nearest: 20 });
spy.on("update", (book) => {
  console.log(String(book));
  for (const row of book) console.log(row.strike, row.call, row.put, row.total);
});
spy.on("status", (status) => console.log("status:", status));
spy.on("error", (error) => console.error("error:", error));
process.on("SIGINT", () => client.close());
client.run().then(() => console.log("closed"));
