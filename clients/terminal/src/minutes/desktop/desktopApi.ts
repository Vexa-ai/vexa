/** The desktop's client half (#1783, desktop ADR §2.3a, D1): the one read (`GET /api/desktop`) held
 *  for this tab, and the person's arranging verbs.
 *
 *  Nothing here is the desk's state — crm-api owns that; this is the last answer read, so the home,
 *  the routine menu and the file menu agree on what is placed. Every arrangement goes through
 *  `arrange(ops)` with the version it was read at (`base_version`): a drag on a section header, a
 *  drag on a tile, Collapse, Hide, Add to desktop and the filter chip's × all write the same ops the
 *  agent's `desktop_arrange` writes, so they share one path and one history. A `desk_changed`
 *  refusal re-reads and tries once more. Each applied change says what changed with Undo, which
 *  restores `undo_version` as a new version. */
import { useEffect, useSyncExternalStore } from "react";
import { readActiveSet } from "../../data/workspaceApi";
import type { Desktop } from "../../platform/desktop";
import { toast } from "../../ui-kit/primitives/Toast";

export type DeskOp = Record<string, unknown> & { op: string };
type Phase = "idle" | "loading" | "ready" | "refused" | "failed";
export type DesktopState = { phase: Phase; desktop: Desktop | null };
type Result = { status: number; body: Record<string, unknown> };

let state: DesktopState = { phase: "idle", desktop: null };
let inflight: Promise<Desktop | null> | null = null;
let subject: string | null = null;
let subjectRead: Promise<string | null> | null = null;
const subs = new Set<() => void>();

function emit(next: DesktopState): void {
  state = next;
  subs.forEach((cb) => cb());
}

/** Test seam: forget the last answer. */
export function resetDesktopStore(): void {
  inflight = null;
  fresh = false;
  subject = null;
  subjectRead = null;
  emit({ phase: "idle", desktop: null });
}

/** Read the desktop (concurrent callers share the read). A refusal (the feature off at crm, 403 or
 *  404) is `refused`: the home keeps what it had. Never throws. */
/** THE HOME FROM ITS LAST ANSWER (founder 2026-10-16, "the app is not very reactive"): drawn at
 *  once from the same signed-in person's last read, then replaced by the fresh one. A convenience in
 *  this browser only; another person's cached home is never drawn. */
const CACHE_KEY = "vexa.cache.desktop";
const OWNER_KEY = "vexa.minutes.chatsOwner";
let fresh = false;
function cachedDesktop(): Desktop | null {
  try {
    const owner = localStorage.getItem(OWNER_KEY);
    const c = JSON.parse(localStorage.getItem(CACHE_KEY) ?? "null") as { owner?: string; desktop?: Desktop } | null;
    return owner && c?.owner === owner && c.desktop && Array.isArray(c.desktop.sections) ? c.desktop : null;
  } catch { return null; }
}
function keepDesktop(d: Desktop): void {
  try {
    const owner = localStorage.getItem(OWNER_KEY);
    if (owner) localStorage.setItem(CACHE_KEY, JSON.stringify({ owner, desktop: d }));
  } catch { /* storage refused: no cache, nothing else changes */ }
}

export function loadDesktop(opts: { force?: boolean } = {}): Promise<Desktop | null> {
  if (inflight) return inflight;
  if (!opts.force && fresh && state.phase === "ready") return Promise.resolve(state.desktop);
  if (state.phase === "idle") {
    const c = cachedDesktop();
    emit(c ? { phase: "ready", desktop: c } : { ...state, phase: "loading" });
  }
  inflight = (async () => {
    try {
      const r = await fetch("/api/desktop", { cache: "no-store" });
      if (r.status === 401 || r.status === 403 || r.status === 404) { emit({ phase: "refused", desktop: null }); return null; }
      if (!r.ok) throw new Error(`desktop ${r.status}`);
      const d = (await r.json()) as Desktop;
      if (!d || !Array.isArray(d.sections)) throw new Error("desktop shape");
      fresh = true;
      keepDesktop(d);
      emit({ phase: "ready", desktop: d });
      return d;
    } catch {
      if (state.phase !== "ready") emit({ phase: "failed", desktop: null });
      return state.desktop;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

const subscribe = (cb: () => void) => { subs.add(cb); return () => { subs.delete(cb); }; };
const snapshot = () => state;

/** React: the last desktop read; starts the read on first use when `load` is true. */
export function useDesktopState(load = true): DesktopState {
  const s = useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => { if (load) void loadDesktop(); }, [load]);
  return s;
}

/** The person's own desk slug (their subject id), for `file:` refs on their own desk. */
export function ensureSubject(): Promise<string | null> {
  if (subject) return Promise.resolve(subject);
  if (!subjectRead) {
    subjectRead = readActiveSet().then((s) => (subject = typeof s?.subject === "string" && s.subject ? s.subject : null)).catch(() => null);
  }
  return subjectRead;
}
export const knownSubject = (): string | null => subject;

async function post(path: string, body: unknown): Promise<Result> {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let parsed: Record<string, unknown> = {};
    try { parsed = (await r.json()) as Record<string, unknown>; } catch { parsed = {}; }
    return { status: r.status, body: parsed && typeof parsed === "object" ? parsed : {} };
  } catch {
    return { status: 0, body: {} };
  }
}

async function baseVersion(): Promise<number | null> {
  const d = state.desktop ?? (await loadDesktop());
  return d ? d.version : null;
}

/** One write with `base_version`; on `desk_changed`, re-read and try once more on the new version. */
async function withBase(path: string, body: Record<string, unknown>): Promise<Result | null> {
  const base = await baseVersion();
  if (base === null) return null;
  let r = await post(path, { ...body, base_version: base });
  if (r.status === 409 && r.body.reason === "desk_changed") {
    const fresh = await loadDesktop({ force: true });
    if (!fresh) return r;
    r = await post(path, { ...body, base_version: fresh.version });
  }
  return r;
}

const applied = (r: Result | null): r is Result => !!r && r.status >= 200 && r.status < 300 && r.body.status === "applied";

/** What a refusal tells the person — one line, never a code. */
function refusalLine(r: Result | null): string {
  const reason = r?.body?.reason;
  if (reason === "desk_changed") return "Your desktop changed elsewhere — try again";
  if (reason === "not_readable") return "You can't read that file, so it can't go on your desktop";
  if (reason === "desk_placements_max") return "Your desktop is full — remove something first";
  return "The desktop couldn't be changed just now";
}

function announce(r: Result): void {
  const summary = typeof r.body.summary === "string" && r.body.summary ? r.body.summary : "Desktop changed";
  const back = r.body.undo_version;
  toast(summary, typeof back === "number" ? { undo: () => { void undoTo(back); } } : undefined);
}

/** Apply ops to the person's own desktop. Resolves true when applied. */
export async function arrange(ops: DeskOp[]): Promise<boolean> {
  const r = await withBase("/api/desktop/arrange", { ops });
  if (!applied(r)) { toast(refusalLine(r)); return false; }
  announce(r);
  await loadDesktop({ force: true });
  return true;
}

/** The same ops, saved quietly in the background: no toast and no re-fetch of the home (a drag's
 *  order is already on screen). Resolves whether it was applied. */
export async function arrangeQuiet(ops: DeskOp[]): Promise<boolean> {
  const r = await withBase("/api/desktop/arrange", { ops });
  return applied(r);
}

/** Undo: restore `version` (layout and placements) as a new version. */
export async function undoTo(version: number): Promise<boolean> {
  const r = await withBase("/api/desktop/undo", { version });
  if (!applied(r)) { toast(refusalLine(r)); return false; }
  toast(typeof r.body.summary === "string" && r.body.summary ? r.body.summary : "Undone");
  await loadDesktop({ force: true });
  return true;
}

/** Apply or dismiss what an agent proposed. Applying offers Undo like any other change. */
export async function decideProposal(id: string, apply: boolean): Promise<boolean> {
  const r = await withBase("/api/desktop/proposal", { proposal_id: id, apply });
  const ok = apply ? applied(r) : !!r && r.status >= 200 && r.status < 300;
  if (!ok) { toast(refusalLine(r)); return false; }
  if (apply) announce(r!);
  await loadDesktop({ force: true });
  return true;
}
