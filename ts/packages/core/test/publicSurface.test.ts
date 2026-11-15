import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as streamWire from "../src/_wire/ws.js";

// The guard list lives in scripts/_leaks.py as digests, so it publishes nothing.
const guard = readFileSync(new URL("../../../../scripts/_leaks.py", import.meta.url), "utf8");
const digests = new Set(guard.match(/"[0-9a-f]{16}"/g)!.map((value) => value.slice(1, -1)));
const wsMessages = new Set(
  guard.match(/WS_MESSAGES = frozenset\(\{([^}]*)\}\)/)![1]!.match(/"(\w+)"/g)!.map((v) => v.slice(1, -1)),
);

function leak(text: string): string | undefined {
  for (const token of text.match(/[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]/g) ?? []) {
    for (const candidate of new Set([token.toLowerCase(), token.toLowerCase().replaceAll("-", "")])) {
      const digest = createHash("sha256").update(candidate).digest("hex").slice(0, 16);
      if (digests.has(digest)) return digest;
    }
  }
  return undefined;
}

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? sources(path) : path.endsWith(".ts") ? [path] : [];
  });
}

describe("public surface", () => {
  it("guards a non-empty list", () => {
    expect(digests.size).toBeGreaterThan(10);
  });

  it("binds only the public WebSocket messages", () => {
    const exported = Object.keys(streamWire).filter((name) => /^[A-Z]/.test(name) && !name.endsWith("FromJSON") && !name.endsWith("ToJSON"));
    expect(exported.filter((name) => !wsMessages.has(name))).toEqual([]);
  });

  it("names nothing an API key cannot use", () => {
    const src = new URL("../src", import.meta.url).pathname;
    const found = sources(src)
      .map((path) => [path.slice(src.length + 1), leak(readFileSync(path, "utf8"))] as const)
      .filter(([, digest]) => digest !== undefined);
    expect(found).toEqual([]);
  });
});
