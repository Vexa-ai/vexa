/** SHOW A PAGE TO EVERYONE IN A CALL (founder 2026-10-15: "people see the same page in a shared
 *  workspace during a call, if they choose to show … pops up a doc for everyone and highlights /
 *  scrolls to the specific things on the doc").
 *
 *  - "Show to everyone" (a page's "…", or the agent's `call_show`) asks agent-api, which checks that
 *    every person in the chat can read the page and sends one `show` message to the room (LiveKit
 *    data, topic `vexa.show`). Refused → the person who showed it is offered to share the workspace
 *    with whoever cannot read it (the existing members-add route), then it is shown.
 *  - Everyone's panel opens the page, titled "Shown by <name>", FOLLOWING the presenter: their scroll
 *    position and their selection (a highlight, scrolled into view) come over the room's data channel.
 *    Anyone can stop following and browse, then follow again.
 *  - Highlights are transient (they clear after a few seconds) unless pinned.
 *  Ephemeral: nothing here is stored; the server writes one line in the chat ("Dmitry showed …").
 *
 *  A module store with a hook, like `callState`: the room (CallRoom) plugs the transport in, the dock
 *  draws the bar, the page menu starts a show. */
import { useSyncExternalStore } from "react";
import { OPEN_PAGE_EVENT } from "../platform/events";
import { getJson } from "../data/apiClient";
import { showToCall, type ShowRefusal } from "../surfaces/callsApi";

export const SHOW_TOPIC = "vexa.show";
export const HIGHLIGHT_MS = 8000;
const HIGHLIGHT_NAME = "vexa-show";

export type ShowEvent =
  | { type: "show"; by: string; workspace: string; path: string; title?: string; anchor?: string }
  | { type: "follow"; by: string; ratio: number }
  | { type: "highlight"; by: string; anchor: string }
  | { type: "stop"; by: string };

export interface Shown { by: string; byName: string; workspace: string; path: string; title: string }
export interface ShowState {
  shown: Shown | null;
  /** this browser is the one showing */
  presenter: boolean;
  /** this browser follows the presenter's scroll and highlights */
  following: boolean;
  /** the current highlight stays until the next */
  pinned: boolean;
  /** a show refused: who cannot read it, to share the workspace with first */
  offer: (ShowRefusal & { page: { workspace: string; path: string; anchor?: string }; session: string }) | null;
  note: string;
}

const initial: ShowState = { shown: null, presenter: false, following: true, pinned: false, offer: null, note: "" };
let state: ShowState = initial;
const subs = new Set<() => void>();
const set = (patch: Partial<ShowState>) => { state = { ...state, ...patch }; subs.forEach((f) => f()); };

export function useCallShow(): ShowState {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state, () => state);
}
export function callShowState(): ShowState { return state; }

// ── the transport the room plugs in ──────────────────────────────────────────────────────────
let send: ((e: ShowEvent) => void) | null = null;
let me = "";
let nameOf: (identity: string) => string = (id) => id;
export function setShowTransport(t: { send: (e: ShowEvent) => void; me: string; nameOf: (id: string) => string } | null) {
  send = t?.send ?? null;
  me = t?.me ?? "";
  if (t) nameOf = t.nameOf;
  if (!t) { stopPresenting(); set({ ...initial }); clearHighlight(); }
}

/** One message from the room (the server's `show`/`highlight`, or the presenter's `follow`/`highlight`). */
export function receiveShow(e: ShowEvent): void {
  if (!e || typeof e !== "object") return;
  if (e.type === "show") {
    const title = e.title || e.path.split("/").pop()?.replace(/\.md$/, "") || e.path;
    const presenter = !!me && e.by === me;
    set({ shown: { by: e.by, byName: presenter ? "you" : nameOf(e.by), workspace: e.workspace, path: e.path, title },
      presenter, following: true, offer: null, note: "" });
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: { workspace: e.workspace, path: e.path } }));
    if (presenter) startPresenting();
    if (e.anchor) highlightWhenReady(e.anchor);
    return;
  }
  if (!state.shown || e.by !== state.shown.by) return;
  if (e.type === "stop") { stopPresenting(); set({ shown: null, presenter: false }); clearHighlight(); return; }
  if (state.presenter || !state.following) return;
  if (e.type === "follow") applyScroll(e.ratio);
  else if (e.type === "highlight") highlightWhenReady(e.anchor);
}

export function stopFollowing(): void { set({ following: false }); }
export function follow(): void { set({ following: true }); }
export function togglePin(): void { set({ pinned: !state.pinned }); if (!state.pinned) scheduleClear(); }
export function stopShowing(): void {
  if (state.presenter && send && state.shown) send({ type: "stop", by: me });
  stopPresenting();
  set({ shown: null, presenter: false });
  clearHighlight();
}

/** "Show to everyone": asks the server; a refusal becomes the offer to share first. */
export async function showPage(session: string, page: { workspace: string; path: string; anchor?: string }): Promise<void> {
  set({ note: "", offer: null });
  try {
    const r = await showToCall(session, page);
    if ("refused" in r) set({ offer: { ...r.refused, page, session } });
  } catch (e) {
    set({ note: e instanceof Error ? e.message : "Couldn't show it to everyone." });
  }
}

/** The offer, accepted: share the workspace with each person who cannot read the page (the person
 *  pressing this is the one in the loop), then show it. */
export async function shareAndShow(f: typeof getJson = getJson): Promise<void> {
  const o = state.offer;
  if (!o) return;
  try {
    for (const m of o.missing) {
      await f("/api/workspace/members/add", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: o.workspace, person: m.subject, role: "contributor" }) });
    }
  } catch (e) {
    set({ note: e instanceof Error ? e.message : "Couldn't share the workspace.", offer: null });
    return;
  }
  await showPage(o.session, o.page);
}
export function dismissOffer(): void { set({ offer: null }); }

// ── the page in the panel: scroll, highlight, and (as presenter) what to send ─────────────────
function docBody(): HTMLElement | null {
  const all = [...document.querySelectorAll<HTMLElement>('[data-pane="pages"] [data-doc-body]')];
  return all.find((x) => x.getClientRects().length > 0) ?? all[0] ?? null;
}
function scroller(): HTMLElement | null {
  let el = docBody();
  while (el) {
    const o = getComputedStyle(el).overflowY;
    if ((o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight) return el;
    el = el.parentElement;
  }
  return docBody();
}
function applyScroll(ratio: number): void {
  const s = scroller();
  if (!s || !Number.isFinite(ratio)) return;
  s.scrollTop = Math.max(0, Math.min(1, ratio)) * Math.max(0, s.scrollHeight - s.clientHeight);
}

/** The anchor on the page: a heading that contains it, else the first text that contains it. */
export function findAnchor(root: HTMLElement, anchor: string): Range | null {
  const want = anchor.trim().toLowerCase();
  if (!want) return null;
  const doc = root.ownerDocument;
  for (const h of root.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
    if ((h.textContent ?? "").toLowerCase().includes(want)) { const r = doc.createRange(); r.selectNodeContents(h); return r; }
  }
  const walk = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const i = (n.textContent ?? "").toLowerCase().indexOf(want);
    if (i >= 0) { const r = doc.createRange(); r.setStart(n, i); r.setEnd(n, i + want.length); return r; }
  }
  return null;
}

let clearTimer: ReturnType<typeof setTimeout> | null = null;
function clearHighlight(): void {
  const hs = (globalThis as { CSS?: { highlights?: Map<string, unknown> } }).CSS?.highlights;
  hs?.delete(HIGHLIGHT_NAME);
  document.querySelectorAll("[data-show-highlight]").forEach((el) => el.removeAttribute("data-show-highlight"));
}
function scheduleClear(): void {
  if (clearTimer) clearTimeout(clearTimer);
  clearTimer = setTimeout(() => { if (!state.pinned) clearHighlight(); }, HIGHLIGHT_MS);
}
/** Highlight it for everyone here and scroll it into view. */
export function applyHighlight(anchor: string): boolean {
  const root = docBody();
  if (!root) return false;
  const range = findAnchor(root, anchor);
  if (!range) return false;
  clearHighlight();
  const g = globalThis as { CSS?: { highlights?: Map<string, unknown> }; Highlight?: new (r: Range) => unknown };
  if (g.CSS?.highlights && g.Highlight) g.CSS.highlights.set(HIGHLIGHT_NAME, new g.Highlight(range));
  else (range.startContainer.parentElement)?.setAttribute("data-show-highlight", "");
  const el = range.startContainer.nodeType === 1 ? range.startContainer as Element : range.startContainer.parentElement;
  el?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  scheduleClear();
  return true;
}
/** The page may still be loading when the message arrives: try for a few seconds. */
function highlightWhenReady(anchor: string, tries = 12): void {
  if (applyHighlight(anchor) || tries <= 0) return;
  setTimeout(() => highlightWhenReady(anchor, tries - 1), 250);
}

// presenter: the scroll position and the selection go to the room
let detach: (() => void) | null = null;
function startPresenting(): void {
  stopPresenting();
  let lastScroll = 0;
  let selTimer: ReturnType<typeof setTimeout> | null = null;
  const onScroll = () => {
    const now = Date.now();
    if (now - lastScroll < 150) return;
    lastScroll = now;
    const s = scroller();
    if (!s || !send) return;
    const span = s.scrollHeight - s.clientHeight;
    send({ type: "follow", by: me, ratio: span > 0 ? s.scrollTop / span : 0 });
  };
  const onSel = () => {
    if (selTimer) clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      const sel = document.getSelection();
      const root = docBody();
      const text = (sel?.toString() ?? "").trim();
      if (!sel || !root || text.length < 3 || !root.contains(sel.anchorNode)) return;
      send?.({ type: "highlight", by: me, anchor: text.slice(0, 300) });
    }, 500);
  };
  document.addEventListener("scroll", onScroll, true);
  document.addEventListener("selectionchange", onSel);
  detach = () => { document.removeEventListener("scroll", onScroll, true); document.removeEventListener("selectionchange", onSel); };
}
function stopPresenting(): void { detach?.(); detach = null; }
