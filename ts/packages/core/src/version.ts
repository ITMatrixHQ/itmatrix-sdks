/**
 * The SDK's own version, the request header that declares it, and the upgrade
 * notice.
 *
 * Server runtimes (Node, Deno, Bun) send `X-ITM-SDK: itmatrix-typescript/<version>`
 * on every request, the same name the stream declares in its hello. Browsers
 * do not by default: a custom header makes every cross-origin call preflight,
 * and an origin whose CORS policy does not allow it would fail outright. Opt in
 * with `new ITMClient({ sdkHeader: true })`.
 *
 * The API may answer `X-ITM-SDK-Current` with the newest release. When this
 * install is behind on the major or minor version, one `console.warn` per
 * module instance names the upgrade command. Patch releases never warn, the
 * notice never throws, and `upgradeNotice: false` or the environment variable
 * `ITMATRIX_NO_UPGRADE_CHECK=1` silences it.
 */

export const SDK_VERSION = "1.2.1";
export const CLIENT_NAME = `itmatrix-typescript/${SDK_VERSION}`;
export const SDK_HEADER = "X-ITM-SDK";
export const CURRENT_HEADER = "X-ITM-SDK-Current";
export const SILENCE_ENV = "ITMATRIX_NO_UPGRADE_CHECK";

interface RuntimeGlobals {
  process?: { versions?: { node?: string }; env?: Record<string, string | undefined> };
  Deno?: unknown;
  Bun?: unknown;
}

const runtime = globalThis as unknown as RuntimeGlobals;

/** True on Node, Deno and Bun; false in browsers and browser workers. */
export function isServerRuntime(): boolean {
  return runtime.process?.versions?.node !== undefined ||
    runtime.Deno !== undefined || runtime.Bun !== undefined;
}

function silenced(): boolean {
  const value = runtime.process?.env?.[SILENCE_ENV]?.trim();
  return value !== undefined && value !== "" && value !== "0";
}

function release(value: string): [number, number] | undefined {
  const match = /^\s*v?(\d+)\.(\d+)/.exec(value);
  return match ? [Number(match[1]), Number(match[2])] : undefined;
}

let warned = false;

/** Warn once when the API advertises a newer minor or major than this install. */
export function noticeCurrent(
  advertised: string | null | undefined,
  warn: (message: string) => void = console.warn,
): void {
  if (warned || !advertised || silenced()) return;
  const installed = release(SDK_VERSION);
  const current = release(advertised);
  if (!installed || !current) return;
  const behind = installed[0] < current[0] ||
    (installed[0] === current[0] && installed[1] < current[1]);
  if (!behind) return;
  warned = true;
  warn(
    `@itmatrixhq/core ${SDK_VERSION} is behind the current release ${advertised.trim()}; ` +
    "upgrade with `npm install @itmatrixhq/core@latest`. " +
    `Set ${SILENCE_ENV}=1 or pass { upgradeNotice: false } to silence this.`,
  );
}

/** @internal Test hook: forget that the notice was shown. */
export function resetUpgradeNoticeForTests(): void {
  warned = false;
}
