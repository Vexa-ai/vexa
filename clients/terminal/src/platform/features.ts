/** Feature switches — which built-but-unproven features this install shows (founder 2026-10-13:
 *  "hide features that are unproven, while having those ready to continue in dev").
 *
 *  agent-api answers `GET /api/features` → `{ features: { <name>: bool }, summaries }`
 *  (`core/agent/control_plane/features.py` is the registry). A switched-off feature keeps its code
 *  and its data; the terminal only hides its entry points, through `useFeature(name)`.
 *
 *  The answer, per name — FAIL CLOSED (R0 hygiene, 2026-10-16):
 *   - present and `true` → on; present and `false` → off;
 *   - absent from the answer → OFF. A flag the server does not name is never on: a branch that adds
 *     a switch here before its server half is registered stays hidden, never shown by accident;
 *   - while the answer is loading, or when it could not be read → OFF for every name (the release
 *     default is off, and an unproven entry point must never flash on first paint).
 *  Every name read here is in SWITCHABLE and registered in the server's FEATURES
 *  (`__tests__/featureRegistry.test.ts` checks both from the repo).
 *
 *  Fetched once, re-read when the window regains focus (at most every 30 s). This folder imports no
 *  other folder of `src/`, so the fetch is a plain `fetch`, not the data layer's client.
 *
 *  TESTS: `setFeatureOverride("all")` (the vitest setup's default) answers every SWITCHABLE name on
 *  without a fetch; a map overrides just those names over all-SWITCHABLE-on; a name outside
 *  SWITCHABLE stays off under either; `null` restores the real answer. The
 *  override lives on `globalThis`, so a test that resets the module registry keeps it. */
import { useEffect, useSyncExternalStore } from "react";

/** Every feature the server can switch off. Hidden until the answer says otherwise. */
export const SWITCHABLE = [
  "shared_chats", "notifications", "calls", "crm", "browser_bots", "folder_drop",
  "chat_history_tools", "calendar_write", "workspace_forget", "agent_messages",
  "home_stream", "people", "event_agents", "desktop", "call_show", "desktop_cards", "transcript_signals",
  // S7 (0.13.4): the workspace page is its README, rendered per viewer; shared pins. Needs home2.
  "readme_page",
  // R1 + R3 (2026-10-16): rooms made in a workspace, chats private; home2 = the new home, the
  // workspace page and the sidebar by workspace
  "rooms", "home2",
  "usage", "levels",
  "public_profiles", "open_workspaces",
  // UI-1 (0.13.4): the approved shell — one-list sidebar, Inbox and Meetings in the centre, My desk,
  // the kit's rows, outlined primaries, two-way panels, the phone fixes. Needs home2.
  "shell2",
] as const;
export type FeatureName = (typeof SWITCHABLE)[number];
/** Switched on only BY NAME — never by "all" (the server's `explicit` features): they change what
 *  people already rely on (rooms; the new home and the sidebar by workspace), so a test or an install
 *  that asks for "all" keeps today's behaviour. */
export const EXPLICIT: readonly string[] = ["rooms", "home2", "shell2", "readme_page"];

/** FAIL CLOSED: on only when the server's answer names it on. A server that does not know the name
 *  (not registered yet, or one release behind) answers nothing about it, and that reads as OFF. */
export const FAIL_CLOSED: readonly string[] = ["transcript_signals"];

type Phase = "idle" | "loading" | "ready" | "failed";
interface State { phase: Phase; features: Record<string, boolean> }

const OVERRIDE_KEY = "__vexaFeatureOverride";
type Override = "all" | Record<string, boolean> | null;
const REFRESH_MS = 30_000;

let state: State = { phase: "idle", features: {} };
/** Bumped on every change (answer or override), so React re-reads `featureOn`. */
let version = 0;
let inflight: Promise<void> | null = null;
let lastRead = 0;
let focusBound = false;
const subs = new Set<() => void>();

function emit(next: State): void {
  state = next;
  version += 1;
  subs.forEach((cb) => cb());
}

function override(): Override {
  return ((globalThis as Record<string, unknown>)[OVERRIDE_KEY] as Override | undefined) ?? null;
}

/** Test seam: `"all"` -> every SWITCHABLE name on but the EXPLICIT ones; a map -> those names as
 *  given, the rest as "all"; an unknown name off either way; `null` -> real. */
export function setFeatureOverride(o: Override): void {
  (globalThis as Record<string, unknown>)[OVERRIDE_KEY] = o;
  version += 1;
  subs.forEach((cb) => cb());
}

/** Test seam: forget the answer (back to "not loaded"). */
export function resetFeatures(): void {
  inflight = null;
  lastRead = 0;
  emit({ phase: "idle", features: {} });
}

function parse(body: unknown): Record<string, boolean> {
  const raw = (body as { features?: unknown } | null)?.features;
  const out: Record<string, boolean> = {};
  if (raw && typeof raw === "object") {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) if (typeof v === "boolean") out[k] = v;
  }
  return out;
}

/** Read `/api/features` (once; concurrent callers share the read). Never throws. */
export function loadFeatures(opts: { force?: boolean } = {}): Promise<void> {
  if (override()) return Promise.resolve();
  if (inflight) return inflight;
  if (!opts.force && state.phase === "ready") return Promise.resolve();
  if (state.phase === "idle") emit({ ...state, phase: "loading" });
  bindFocus();
  inflight = (async () => {
    try {
      const r = await fetch("/api/features", { cache: "no-store" });
      if (!r.ok) throw new Error(`features ${r.status}`);
      emit({ phase: "ready", features: parse(await r.json()) });
    } catch {
      // A failed read keeps a previous answer; with none, the switchable names stay hidden.
      if (state.phase !== "ready") emit({ ...state, phase: "failed" });
    } finally {
      lastRead = Date.now();
      inflight = null;
    }
  })();
  return inflight;
}

function bindFocus(): void {
  if (focusBound || typeof window === "undefined") return;
  focusBound = true;
  window.addEventListener("focus", () => {
    if (Date.now() - lastRead >= REFRESH_MS) void loadFeatures({ force: true });
  });
}

/** Is this feature on, right now? (See the module comment for the rule.) */
export function featureOn(name: string): boolean {
  const o = override();
  if (o) {
    if (o !== "all" && Object.prototype.hasOwnProperty.call(o, name)) return o[name] === true;
    return (SWITCHABLE as readonly string[]).includes(name) && !EXPLICIT.includes(name);
  }
  if (state.phase === "ready") return state.features[name] === true;
  return false;
}

function subscribe(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
const snapshot = () => version;

/** React: is this feature on? Starts the one read on first use; re-renders when the answer lands. */
export function useFeature(name: FeatureName | string): boolean {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => { void loadFeatures(); }, []);
  return featureOn(name);
}

/** React, for a page that IS the feature (not an entry point): `undefined` while the answer is still
 *  loading, so the page can wait instead of first saying "not switched on" and then rendering. */
export function useFeatureAnswer(name: FeatureName | string): boolean | undefined {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => { void loadFeatures(); }, []);
  if (!override() && (state.phase === "idle" || state.phase === "loading")) return undefined;
  return featureOn(name);
}
