"use client";
/** DELETING A PAGE — founder, 2026-10-11, on a junk entity page the agent had written from a
 *  misheard transcript fragment: *"we probably need a delete for a note"*.
 *
 *  ONE WRITER. The delete is agent-api's `POST /api/workspace/remove` — the verb the agent's own
 *  `workspace_delete` calls — and the undo is `POST /api/workspace/restore`. Both commit, so a
 *  deleted page is history, never a loss, and both authorize exactly as a save does. Nothing here
 *  decides who may delete: `canDeleteIn` only decides whether to OFFER the control, and an offer
 *  that turns out wrong is answered by the server's refusal, shown in the dialog.
 *
 *  THREE PIECES, one module, so the page header and the navigator ask the same question the same
 *  way: the confirm dialog, the act (`deletePage`, which also tells the shell to drop the page's
 *  tabs), and the toast with **Undo**. The toast is a module-level store rendered once by the pages
 *  panel, because the navigator that started a delete may be closed before Undo is pressed. */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { PAGE_REMOVED_EVENT, WORKSPACE_COMMIT_EVENT } from "../platform";
import { Modal } from "../ui-kit/Modal";
import { invalidateDocLinkCaches } from "../ui-kit/docLinks";
import { presentError } from "../surfaces/apiClient";
import {
  listSharedMemberships, readWorkspaceBySlug, removeWorkspaceFile, restoreWorkspaceFile,
} from "../surfaces/workspaceApi";
import { navigateView } from "./roomView";
import { type as ty } from "./tokens";

/** What is being deleted, as the dialog and the toast name it. `slug` absent = the reader's desk. */
export interface PageTarget { path: string; slug?: string; title: string; workspace: string }

export const GLOBAL = "_global";
const SYSTEM = "_system";

/** A page's title for the dialog: its file name without `.md`. */
export const pageTitle = (path: string): string => (path.split("/").pop() || path).replace(/\.md$/i, "");

/** What the reader calls a workspace in a sentence. The desk has no slug in the panel. */
export const workspaceLabel = (slug: string | undefined): string =>
  !slug || /^\d+$/.test(slug) ? "your personal workspace" : slug;

// ── who is OFFERED Delete ─────────────────────────────────────────────────────────────────────────

const isAdmin = async (): Promise<boolean> => {
  try {
    const r = await fetch("/api/auth/me", { cache: "no-store" });
    if (!r.ok) return false;
    const me = (await r.json()) as { is_admin?: boolean; admin_account?: boolean } | null;
    return me?.admin_account === true || me?.is_admin === true;
  } catch { return false; }
};

/** May this reader be OFFERED Delete in `slug`? The edit rule, read from what the client can see:
 *  their own desk yes; `_system` never; `_global` only its admin; a shared workspace when they are
 *  an owner or contributor; any other workspace when its identity says they may write it. A reader
 *  gets no Delete. A failed read offers nothing — the button is the cheap thing to lose. */
export async function canDeleteIn(slug: string | undefined): Promise<boolean> {
  if (!slug) return true;
  if (slug === SYSTEM || slug.startsWith(".system")) return false;
  if (slug === GLOBAL) return isAdmin();
  try {
    const mine = (await listSharedMemberships()).find((m) => m.workspace_id === slug);
    if (mine) return mine.role === "owner" || mine.role === "contributor";
  } catch { /* fall through to the identity read */ }
  try { return (await readWorkspaceBySlug(slug)).writable === true; } catch { return false; }
}

const offerCache = new Map<string, Promise<boolean>>();
/** `canDeleteIn`, asked once per workspace per page load. */
export function offerDelete(slug: string | undefined): Promise<boolean> {
  const key = slug ?? "";
  let p = offerCache.get(key);
  if (!p) { p = canDeleteIn(slug); offerCache.set(key, p); }
  return p;
}
export function useCanDelete(slug: string | undefined): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    let live = true;
    setOk(false);
    void offerDelete(slug).then((v) => { if (live) setOk(v); });
    return () => { live = false; };
  }, [slug]);
  return ok;
}
/** test seam */
export const resetOfferCache = () => offerCache.clear();

// ── the tabs ─────────────────────────────────────────────────────────────────────────────────────

/** THE STRIP WITHOUT THE DELETED PAGE. Every tab on that page goes — preview and pins alike: a tab
 *  on a page that is gone is a door to "no page here yet". The chat's home and a meeting's own
 *  pages stay, as every other close path leaves them. Pure, so the shell's one writer applies it. */
export function dropRemovedPage<T extends { kind?: string; path: string; slug?: string; desk?: boolean; permanent?: boolean }>(
  list: readonly T[], path: string, slug: string | undefined,
): T[] {
  return list.filter((a) => !(a.kind !== "meeting" && a.path === path && (a.slug || undefined) === (slug || undefined)
    && !a.desk && !a.permanent));
}

// ── the toast with Undo ───────────────────────────────────────────────────────────────────────────

type Removed = { target: PageTarget; commit: string | null; at: number; undoing?: boolean; error?: string };
let removed: Removed | null = null;
const listeners = new Set<() => void>();
const emit = () => { for (const l of listeners) l(); };
const setRemoved = (r: Removed | null) => { removed = r; emit(); };
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

/** How long Undo stays on screen. After that the page is in History. */
export const UNDO_MS = 10_000;

const settle = () => {
  invalidateDocLinkCaches();   // a link to the page must resolve again: dashed when gone, solid when back
  window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT));
};

/** THE ACT. Removes the page, tells the shell to drop its tabs, and offers Undo. Throws the server's
 *  refusal for the dialog to show. */
export async function deletePage(target: PageTarget): Promise<void> {
  const r = await removeWorkspaceFile(target.path, target.slug ? { slug: target.slug } : undefined);
  window.dispatchEvent(new CustomEvent(PAGE_REMOVED_EVENT, { detail: { path: target.path, slug: target.slug } }));
  settle();
  setRemoved({ target, commit: r.commit ?? null, at: Date.now() });
}

/** UNDO: put the page back from the removal commit and bring it into view. */
export async function undoDelete(): Promise<void> {
  const r = removed;
  if (!r || r.undoing) return;
  setRemoved({ ...r, undoing: true, error: undefined });
  try {
    await restoreWorkspaceFile(r.target.path, { slug: r.target.slug, commit: r.commit });
    setRemoved(null);
    settle();
    navigateView(r.target.slug, r.target.path);
  } catch (e) {
    setRemoved({ ...r, undoing: false, error: presentError(e).headline });
  }
}

/** HISTORY'S RESTORE: the same route as Undo, from a removal row in a workspace's History. */
export async function restoreFromHistory(t: { path: string; slug?: string; commit: string }): Promise<void> {
  await restoreWorkspaceFile(t.path, { slug: t.slug, commit: t.commit });
  settle();
  navigateView(t.slug, t.path);
}

/** WHICH PAGE A HISTORY ROW REMOVED, or null. agent-api writes a removal's subject as
 *  `<workspace>: <path> — removed`, shortening a long path in the middle (`…`) so the verb always
 *  survives; a shortened path is read off the commit's files instead. */
export function removedPathOf(c: { msg: string; files?: string[] }): string | null {
  const m = /^[^:]+: (.+) — removed$/.exec(c.msg ?? "");
  if (!m) return null;
  if (!m[1].startsWith("…")) return m[1];
  const tail = m[1].slice(1);
  return (c.files ?? []).find((f) => f !== "kg/INDEX.md" && f.endsWith(tail)) ?? null;
}

export const dismissRemoved = () => setRemoved(null);
/** test seam */
export const removedNow = () => removed;

export function PageRemovedToast() {
  const r = useSyncExternalStore(subscribe, () => removed, () => null);
  useEffect(() => {
    if (!r || r.undoing || r.error) return;
    const t = setTimeout(() => { if (removed === r) setRemoved(null); }, UNDO_MS);
    return () => clearTimeout(t);
  }, [r]);
  if (!r || typeof document === "undefined") return null;
  return createPortal(
    <div data-page-removed role="status" style={{
      position: "fixed", bottom: 20, left: "50%", transform: "translateX(-50%)", zIndex: 1100,
      display: "flex", alignItems: "center", gap: 12, padding: "8px 10px 8px 14px", maxWidth: "min(520px, calc(100vw - 32px))",
      background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10, boxShadow: "0 8px 28px rgba(0,0,0,.35)",
    }}>
      <span style={{ ...ty.body, fontSize: 13, color: r.error ? "var(--danger)" : "var(--t1)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {r.error ? `Could not restore: ${r.error}` : <>Deleted <b>{r.target.title}</b>.</>}
      </span>
      <button data-page-undo onClick={() => void undoDelete()} disabled={r.undoing}
        style={{ ...ty.chip, flex: "none", background: "transparent", border: "none", color: "var(--accent)", fontWeight: 600, cursor: r.undoing ? "default" : "pointer", padding: "3px 6px" }}>
        {r.undoing ? "Restoring…" : "Undo"}</button>
      <button aria-label="Dismiss" onClick={dismissRemoved}
        style={{ flex: "none", background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 15, lineHeight: 1, padding: "0 4px" }}>×</button>
    </div>,
    document.body,
  );
}

// ── the confirm ───────────────────────────────────────────────────────────────────────────────────

/** "Delete <title>?" — the button names the act and takes focus ONCE, when the dialog opens, so
 *  Enter deletes and Esc cancels (the #1808 / #1829 rule: the reader opened this by choosing
 *  Delete, and a focus that a re-render moves back to Cancel turns Enter into "nothing happened").
 *  The effect has no dependencies on purpose: the shell re-renders on every poll. */
export function DeletePageDialog(p: { target: PageTarget; onClose: () => void; onDeleted?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => { confirm.current?.focus(); }, []);
  const run = async () => {
    if (busy) return;
    setBusy(true); setError(null);
    try { await deletePage(p.target); p.onClose(); p.onDeleted?.(); }
    catch (e) { setError(presentError(e).headline); setBusy(false); }
  };
  return (
    <Modal title={`Delete ${p.target.title}?`} onClose={p.onClose}>
      <div data-page-delete-dialog style={{ display: "flex", flexDirection: "column" }}>
        <p style={{ fontSize: 13, color: "var(--t2)", margin: 0, lineHeight: 1.6 }}>
          It&apos;s removed from <b style={{ color: "var(--t1)" }}>{p.target.workspace}</b>; you can restore it from History.
        </p>
        {error && <p data-page-delete-error role="alert" style={{ fontSize: 12, color: "var(--danger)", margin: "9px 0 0", lineHeight: 1.5 }}>Could not delete: {error}</p>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <button data-page-delete-cancel disabled={busy} onClick={p.onClose}
            style={{ fontSize: 13, padding: "8px 16px", background: "transparent", color: "var(--t2)", border: "1px solid var(--line)", borderRadius: 8, cursor: "pointer" }}>Cancel</button>
          <button ref={confirm} data-page-delete-confirm disabled={busy} onClick={() => void run()}
            style={{ fontSize: 13, padding: "8px 16px", background: "var(--danger)", color: "var(--bg)", border: "none", borderRadius: 8, cursor: busy ? "default" : "pointer", fontWeight: 600 }}>
            {busy ? "Deleting…" : "Delete page"}</button>
        </div>
      </div>
    </Modal>
  );
}
