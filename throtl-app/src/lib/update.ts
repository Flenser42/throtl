// The only outbound request the app makes: a GET on the public GitHub release
// API (no account, no identifier, no user data). It mirrors the logic in
// `throtl/version.py`, but lives in the webview because the daemon must not do
// network I/O. Any failure yields "no update" — never an error in the UI.

export const REPOSITORY = "Flenser42/throtl";
export const PROJECT_URL = `https://github.com/${REPOSITORY}`;
export const RELEASES_URL = `${PROJECT_URL}/releases`;
const LATEST_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const TIMEOUT_MS = 4000;

/** `tag_name` without the leading `v`. */
export function parseTag(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const tag = String((payload as { tag_name?: unknown }).tag_name ?? "").trim();
  if (!tag) return null;
  return tag[0] === "v" || tag[0] === "V" ? tag.slice(1) : tag;
}

type VersionKey = [number[], number, Array<[number, number | string]>];

/** Semver-ish key: build metadata after `+` is ignored and a pre-release sorts
 * before its release (`1.0.0` > `1.0.0-rc1`). */
function versionKey(version: string | null | undefined): VersionKey | null {
  if (!version) return null;
  const text = String(version).trim().replace(/^[vV]/, "");
  if (!text) return null;
  const core = text.split("+", 1)[0];
  let numeric: string;
  let isRelease: number;
  let identifiers: Array<[number, number | string]> = [];
  if (core.includes("-")) {
    const dash = core.indexOf("-");
    numeric = core.slice(0, dash);
    isRelease = 0;
    identifiers = core
      .slice(dash + 1)
      .split(".")
      .filter((part) => part !== "")
      .map((part) =>
        /^\d+$/.test(part) ? ([0, Number(part)] as [number, number]) : ([1, part] as [number, string]),
      );
  } else {
    numeric = core;
    isRelease = 1;
  }
  const parts: number[] = [];
  for (const chunk of numeric.split(".")) {
    if (!/^\d+$/.test(chunk)) return null;
    parts.push(Number(chunk));
  }
  if (parts.length === 0) return null;
  return [parts, isRelease, identifiers];
}

function compareKeys(a: VersionKey, b: VersionKey): number {
  const [aNum, aRel, aPre] = a;
  const [bNum, bRel, bPre] = b;
  const width = Math.max(aNum.length, bNum.length);
  for (let i = 0; i < width; i += 1) {
    const x = aNum[i] ?? 0;
    const y = bNum[i] ?? 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  if (aRel !== bRel) return aRel < bRel ? -1 : 1;
  const len = Math.min(aPre.length, bPre.length);
  for (let i = 0; i < len; i += 1) {
    const [aNumeric, aValue] = aPre[i];
    const [bNumeric, bValue] = bPre[i];
    if (aNumeric !== bNumeric) return aNumeric < bNumeric ? -1 : 1;
    if (aValue !== bValue) return aValue < bValue ? -1 : 1;
  }
  if (aPre.length !== bPre.length) return aPre.length < bPre.length ? -1 : 1;
  return 0;
}

/** Is `latest` newer than `current`? An unreadable version means "not newer":
 * better no hint than a wrong one. */
export function isNewer(current: string, latest: string): boolean {
  const a = versionKey(current);
  const b = versionKey(latest);
  if (!a || !b) return false;
  return compareKeys(b, a) > 0;
}

/** Latest release version, or null on any error. */
export async function fetchLatest(): Promise<string | null> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(LATEST_API, {
      headers: { Accept: "application/vnd.github+json" },
      signal: controller.signal,
    });
    if (!response.ok) return null;
    return parseTag(await response.json());
  } catch {
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}
