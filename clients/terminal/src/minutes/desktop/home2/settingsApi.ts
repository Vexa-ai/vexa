/** THE PERSON'S OWN SETTINGS ABOUT EACH WORKSPACE (#1783, R3 3.4). Founder-approved rule: per-person
 *  state about a workspace lives on the CRM `member_of` relation, written only by crm-api, in the
 *  person's own CRM — never in browser storage, never in an agent-api table. This module holds the
 *  last answer for this tab and writes through `/api/desktop/workspace-settings`; a change is shown at
 *  once and rolled back if crm-api refuses it. */
import { useEffect, useSyncExternalStore } from "react";
import { toast } from "../../../ui-kit/primitives/Toast";

export type Notify = "all" | "mentions" | "none";
export type WorkspaceSettings = {
  pinned: boolean; hidden: boolean; muted: boolean; notify: Notify; room_order: string[];
  level: number | null; collapsed: string[]; updated_at: string | null;
};
export const DEFAULT_SETTINGS: WorkspaceSettings = { pinned: false, hidden: false, muted: false, notify: "all", room_order: [], level: null, collapsed: [], updated_at: null };

type State = { phase: "idle" | "loading" | "ready" | "off"; all: Record<string, WorkspaceSettings> };
let state: State = { phase: "idle", all: {} };
let inflight: Promise<void> | null = null;
const subs = new Set<() => void>();
const emit = (s: State) => { state = s; subs.forEach((f) => f()); };

/** Test seam. */
export function resetSettings(): void { inflight = null; emit({ phase: "idle", all: {} }); }

export function loadSettings(): Promise<void> {
  if (inflight) return inflight;
  if (state.phase === "ready" || state.phase === "off") return Promise.resolve();
  emit({ ...state, phase: "loading" });
  inflight = (async () => {
    try {
      const r = await fetch("/api/desktop/workspace-settings", { cache: "no-store" });
      if (!r.ok) { emit({ phase: "off", all: {} }); return; }
      const d = (await r.json()) as { workspaces?: Record<string, Partial<WorkspaceSettings>> };
      const all: Record<string, WorkspaceSettings> = {};
      for (const [k, v] of Object.entries(d?.workspaces ?? {})) all[k] = { ...DEFAULT_SETTINGS, ...v };
      emit({ phase: "ready", all });
    } catch {
      emit({ phase: "off", all: {} });
    } finally { inflight = null; }
  })();
  return inflight;
}

export function settingsOf(slug: string): WorkspaceSettings { return state.all[slug] ?? DEFAULT_SETTINGS; }

/** Change some of one workspace's settings, as the person. Shown at once; rolled back on a refusal. */
export async function setSettings(slug: string, patch: Partial<WorkspaceSettings>): Promise<boolean> {
  const before = state.all[slug];
  emit({ ...state, all: { ...state.all, [slug]: { ...(before ?? DEFAULT_SETTINGS), ...patch } } });
  try {
    const r = await fetch("/api/desktop/workspace-settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: slug, settings: patch }) });
    if (!r.ok) throw new Error(String(r.status));
    const d = (await r.json()) as { settings?: Partial<WorkspaceSettings> };
    if (d?.settings) emit({ ...state, all: { ...state.all, [slug]: { ...DEFAULT_SETTINGS, ...d.settings } } });
    return true;
  } catch {
    const all = { ...state.all };
    if (before) all[slug] = before; else delete all[slug];
    emit({ ...state, all });
    toast("That setting couldn't be saved just now");
    return false;
  }
}

const sub = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const snap = () => state;

export function useWorkspaceSettings(): { phase: State["phase"]; of: (slug: string) => WorkspaceSettings } {
  const s = useSyncExternalStore(sub, snap, snap);
  useEffect(() => { void loadSettings(); }, []);
  return { phase: s.phase, of: (slug: string) => s.all[slug] ?? DEFAULT_SETTINGS };
}

/** The home's order: pinned first, then as the desk arranged them; hidden ones apart. */
export function homeOrder<T extends { slug?: string }>(items: T[], of: (slug: string) => WorkspaceSettings): { shown: T[]; hidden: T[] } {
  const slugOf = (i: T) => i.slug || "personal";
  const shown = items.filter((i) => !of(slugOf(i)).hidden);
  const hidden = items.filter((i) => of(slugOf(i)).hidden);
  return { shown: [...shown.filter((i) => of(slugOf(i)).pinned), ...shown.filter((i) => !of(slugOf(i)).pinned)], hidden };
}
