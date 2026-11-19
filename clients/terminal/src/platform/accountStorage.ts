/** Browser state belongs to the account that wrote it.
 *
 *  Every `vexa.*` key in localStorage and sessionStorage — the open workspace, a pending meeting or
 *  preset, the composed view, the dock and shell layout, chat drafts — is stored under the signed-in
 *  account: `vexa.openWorkspace` is kept as `vexa:<account>:openWorkspace`. Callers keep using the
 *  plain names; `installAccountScope` rewrites them at the Storage seam, so a key added later is
 *  scoped without anyone remembering to.
 *
 *  How it works. The account is not known synchronously (the user cookie is httpOnly), so the scope
 *  at boot is the account that last used this browser (`vexa.account`). AuthGate calls
 *  `adoptAccount(id)` with the account `/api/auth/me` validated, before anything renders:
 *   • same account → nothing changes;
 *   • first account on this browser → unscoped legacy `vexa.*` keys are removed, never applied;
 *   • a different account → the scope moves to it and the caller reloads, so state a module read at
 *     load under the previous account is read again under this one.
 *  An unverified account (identity unreachable, or `user.id` null) gets a scope of its own that
 *  holds nothing and is emptied each time. If a known account's scope was active, `vexa.account` is
 *  removed and the caller reloads, so the previous account's state is never shown; after that
 *  reload the answer is `unverified` and no further reload is asked for.
 *
 *  Exempt: `vexa.account` itself and `vexa.terminal.theme` — the theme is read by an inline script
 *  before any account is known (no flash of the wrong theme) and says nothing about an account. */

export const ACCOUNT_KEY = "vexa.account";
const PREFIX = "vexa.";
const EXEMPT = new Set<string>([ACCOUNT_KEY, "vexa.terminal.theme"]);
const UNVERIFIED = "?";

type Native = { get: Storage["getItem"]; set: Storage["setItem"]; remove: Storage["removeItem"] };
let native: Native | null = null;
let scope: string | null = null;

/** The stored name of `key` for `account`; keys outside the `vexa.` namespace pass through. */
export function scopedKey(key: string, account: string | null = scope): string {
  if (!key.startsWith(PREFIX) || EXEMPT.has(key) || account === null) return key;
  return `vexa:${account}:${key.slice(PREFIX.length)}`;
}

function raw(): Native {
  if (native) return native;
  const p = Storage.prototype;
  return { get: p.getItem, set: p.setItem, remove: p.removeItem };
}

function storages(): Storage[] {
  const out: Storage[] = [];
  try { out.push(window.localStorage); } catch { /* storage unavailable */ }
  try { out.push(window.sessionStorage); } catch { /* storage unavailable */ }
  return out;
}

/** Remove every unscoped `vexa.*` key (except the exempt ones) from both storages. */
function clearLegacy(): void {
  const n = raw();
  for (const s of storages()) {
    const doomed: string[] = [];
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k && k.startsWith(PREFIX) && !EXEMPT.has(k)) doomed.push(k);
    }
    doomed.forEach((k) => n.remove.call(s, k));
  }
}

/** Remove everything stored under one scope (the unverified one holds only this load's writes). */
function clearScope(account: string): void {
  const n = raw();
  const head = `vexa:${account}:`;
  for (const s of storages()) {
    const doomed: string[] = [];
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k && k.startsWith(head)) doomed.push(k);
    }
    doomed.forEach((k) => n.remove.call(s, k));
  }
}

/** Route every Storage read and write of a `vexa.*` key through the current account's scope. */
export function installAccountScope(): void {
  if (native || typeof Storage === "undefined") return;
  const p = Storage.prototype;
  native = { get: p.getItem, set: p.setItem, remove: p.removeItem };
  const n = native;
  try { scope = n.get.call(window.localStorage, ACCOUNT_KEY); } catch { scope = null; }
  // No account has used this browser yet: nothing is readable until one is adopted.
  if (scope === null) scope = UNVERIFIED;
  p.getItem = function getItem(this: Storage, key: string) { return n.get.call(this, scopedKey(String(key))); };
  p.setItem = function setItem(this: Storage, key: string, value: string) { n.set.call(this, scopedKey(String(key)), value); };
  p.removeItem = function removeItem(this: Storage, key: string) { n.remove.call(this, scopedKey(String(key))); };
}

/** Restore the native Storage methods (tests). */
export function uninstallAccountScope(): void {
  if (!native) return;
  const p = Storage.prototype;
  p.getItem = native.get; p.setItem = native.set; p.removeItem = native.remove;
  native = null;
  scope = null;
}

/** The validated account is `account` (null = could not be verified). Returns what changed:
 *  `same` (nothing), `first` (first account on this browser; legacy keys cleared), or `changed`
 *  (another account's scope was active — the caller reloads so nothing read under it stays; this
 *  includes an unverified answer while a known account was active, which also clears `vexa.account`),
 *  or `unverified` (no account and none was active: an empty scope of its own, and no reload). */
export function adoptAccount(account: string | number | null | undefined): "same" | "first" | "changed" | "unverified" {
  const n = raw();
  const next = account === null || account === undefined || String(account).trim() === ""
    ? UNVERIFIED : String(account).trim().replace(/:/g, "_");
  let stored: string | null = null;
  try { stored = n.get.call(window.localStorage, ACCOUNT_KEY); } catch { /* storage unavailable */ }
  const before = scope;
  scope = next;
  if (next === UNVERIFIED) {
    clearScope(UNVERIFIED);
    // A known account's scope was active: forget it and reload, so nothing read under it renders.
    if (stored !== null || (before !== null && before !== UNVERIFIED)) {
      try { n.remove.call(window.localStorage, ACCOUNT_KEY); } catch { /* storage unavailable */ }
      return "changed";
    }
    return "unverified";
  }
  try { n.set.call(window.localStorage, ACCOUNT_KEY, next); } catch { /* storage unavailable */ }
  clearLegacy();
  if (stored === next && before === next) return "same";
  if (stored === null && (before === UNVERIFIED || before === null)) { clearScope(UNVERIFIED); return "first"; }
  clearScope(UNVERIFIED);
  return "changed";
}
