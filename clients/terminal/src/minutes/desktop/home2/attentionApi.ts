/** What needs the person, and what is working where — the client half of decision E1 (#1783, R3,
 *  feature `home2`).
 *
 *  agent-api composes it per request from the facts' own owners (proposals, notifications, crm cards
 *  via the BFF, meetings, flows), by rules, with no model call and no store: `POST
 *  /api/desktop/attention`. This module holds the last answer for this tab so the status cards, the
 *  Now strip and the page badges agree; it is re-read every 30 s while visible, on focus, and after an
 *  act. One workspace's richer sections (`workspace`) are read on their own for the workspace page.
 *  Nothing here decides what needs you: an item is drawn exactly as the server sent it. */
import { useEffect, useSyncExternalStore } from "react";

export type NeedKind = "proposal" | "mention" | "card" | "upcoming" | "failed";
export type ActKind = "accept-proposal" | "open-session" | "open-page" | "open-meeting" | "prepare" | "retry-routine" | "open-card";
export type Act = {
  label: string; kind: ActKind;
  proposal?: string; session?: string; path?: string; slug?: string; meeting?: string; routine?: string; card?: string;
} & Record<string, unknown>;
export type Need = { id: string; kind: NeedKind; workspace: string; text: string; at: number; act: Act };
export type Work = {
  kind: "routine" | "call" | "room"; label: string; state: "running" | "ok" | "failed" | "next" | "paused" | "live";
  when: string | null; at: number | null; ref?: string; session?: string; meeting?: string;
};
export type LiveCall = { session?: string; meeting?: string; platform?: string; line?: string; people?: number; since: number };
export type Run = { at: number; ok: boolean; what: string };
export type WorkspaceAttention = {
  needs: Need[]; working: Work[]; rooms_active: number; live_call: LiveCall | null; last: string | null;
  /** only in the one-workspace read */
  rooms?: { session: string; name: string; members: number; last_active: number; unread?: number; closed?: boolean }[];
  chats?: { session: string; name: string; last_active: number }[];
  routines?: { ref: string; name: string; state: string; when: string | null; last_at: number | null; next_at: number | null; runs: Run[] }[];
  calls?: { meeting: string; name: string; at: number; state: "live" | "ended" | "upcoming" }[];
};
export type Attention = { generated_at: number; needs: Need[]; workspaces: Record<string, WorkspaceAttention> };

const EMPTY: WorkspaceAttention = { needs: [], working: [], rooms_active: 0, live_call: null, last: null };
const REFRESH_MS = 30_000;

type Phase = "idle" | "loading" | "ready" | "refused" | "failed";
let state: { phase: Phase; data: Attention | null } = { phase: "idle", data: null };
let inflight: Promise<void> | null = null;
let lastRead = 0;
const subs = new Set<() => void>();
const emit = (next: typeof state) => { state = next; subs.forEach((f) => f()); };

/** Test seam. */
export function resetAttention(): void { inflight = null; lastRead = 0; emit({ phase: "idle", data: null }); }

async function post(body: Record<string, unknown>): Promise<{ status: number; data: Attention | null }> {
  try {
    const r = await fetch("/api/desktop/attention", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
    if (!r.ok) return { status: r.status, data: null };
    const d = (await r.json()) as Attention;
    return { status: r.status, data: d && typeof d === "object" && d.workspaces ? { ...d, needs: Array.isArray(d.needs) ? d.needs : [] } : null };
  } catch {
    return { status: 0, data: null };
  }
}

export function loadAttention(opts: { force?: boolean } = {}): Promise<void> {
  if (inflight) return inflight;
  if (!opts.force && state.phase === "ready" && Date.now() - lastRead < REFRESH_MS) return Promise.resolve();
  if (state.phase === "idle") emit({ ...state, phase: "loading" });
  inflight = (async () => {
    const { status, data } = await post({});
    lastRead = Date.now();
    if (data) emit({ phase: "ready", data });
    else if (state.phase !== "ready") emit({ phase: status === 403 || status === 404 ? "refused" : "failed", data: null });
    inflight = null;
  })();
  return inflight;
}

/** The last answer read (null before the first). */
export function attentionSnapshot(): Attention | null { return state.data; }

function subscribe(cb: () => void): () => void { subs.add(cb); return () => { subs.delete(cb); }; }
const snap = () => state;

/** The whole answer; starts the read, keeps it fresh while the page is visible. */
export function useAttention(): { phase: Phase; data: Attention | null } {
  const s = useSyncExternalStore(subscribe, snap, snap);
  useEffect(() => {
    void loadAttention();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void loadAttention({ force: true }); }, REFRESH_MS);
    const onFocus = () => void loadAttention();
    window.addEventListener("focus", onFocus);
    return () => { window.clearInterval(t); window.removeEventListener("focus", onFocus); };
  }, []);
  return s;
}

/** One workspace's part of the home answer (empty while loading or for a key the server did not send). */
export function workspaceAttention(data: Attention | null, slug: string): WorkspaceAttention {
  return data?.workspaces?.[slug] ?? EMPTY;
}

/** One workspace with its richer sections (the workspace page). */
export async function readWorkspaceAttention(slug: string): Promise<WorkspaceAttention | null> {
  const { data } = await post({ workspace: slug });
  return data ? data.workspaces?.[slug] ?? EMPTY : null;
}
