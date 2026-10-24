/** The account's mode — identity.v1 `account_mode`, as `GET /api/auth/me` says it.
 *
 *  `claude` is «Vexa for Claude» (founder ruling 2026-10-24): Vexa runs no agent for the account —
 *  its own Claude listens to its calls and writes — so the terminal shows its calls and workspaces and
 *  no agent surface (chat composer, Home cards, routines, notes, listening). agent-api refuses the
 *  agent turns either way; this only decides what is drawn.
 *
 *  `null` while the answer is loading or when it could not be read. Read once per page load and
 *  shared by every caller (one fetch, one store). A surface hides on a literal `"claude"` only, so an
 *  older identity or a failed read shows the standard terminal.
 *
 *  TESTS: `setAccountModeOverride("claude")` answers without a fetch; `null` restores the real answer. */
import { useEffect, useSyncExternalStore } from "react";

export type AccountMode = "standard" | "claude";

const OVERRIDE_KEY = "__vexaAccountModeOverride";
let mode: AccountMode | null = null;
let inflight: Promise<void> | null = null;
let loaded = false;
const subs = new Set<() => void>();

function emit(next: AccountMode | null): void {
  mode = next;
  subs.forEach((cb) => cb());
}

function override(): AccountMode | null | undefined {
  return (globalThis as Record<string, unknown>)[OVERRIDE_KEY] as AccountMode | null | undefined;
}

/** Test seam: answer `m` without a fetch; `null` restores the real answer. */
export function setAccountModeOverride(m: AccountMode | null): void {
  (globalThis as Record<string, unknown>)[OVERRIDE_KEY] = m ?? undefined;
  subs.forEach((cb) => cb());
}

/** Read `/api/auth/me` once; concurrent callers share the read. Never throws. */
export function loadAccountMode(): Promise<void> {
  if (override() || loaded) return Promise.resolve();
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store" });
      const body = r.ok ? ((await r.json()) as { account_mode?: unknown }) : null;
      const said = body?.account_mode;
      if (said === "claude" || said === "standard") {
        loaded = true;
        emit(said);
      }
    } catch {
      /* unknown stays null: the standard terminal */
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function accountMode(): AccountMode | null {
  return override() ?? mode;
}

/** The account's mode, re-rendering when it arrives. */
export function useAccountMode(): AccountMode | null {
  useEffect(() => { void loadAccountMode(); }, []);
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => { subs.delete(cb); }; },
    accountMode,
    () => null,
  );
}
