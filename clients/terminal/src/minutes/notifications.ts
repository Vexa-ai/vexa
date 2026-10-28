/** NOTIFICATIONS — what happened to this person that they did not do themselves (#1783).
 *
 *  Founder, 2026-10-13: *"we need notifications popup in UI when people invite each other etc"*.
 *
 *  agent-api keeps them per person (`GET /api/notifications`, `POST /api/notifications/read`) and
 *  publishes each new one on the person's own channel, which the gateway's `/ws` fans in for every
 *  tab (`gatewayWS.onUserFrame`). So: on load, read the unread ones; while open, take them live.
 *  One module-level store, shared by the bell and the toasts, so both say the same count.
 *
 *  A notification names who did what to which object — a chat, a workspace — and never carries what
 *  was said (the server composes the text; this only renders it). Opening one opens its object.
 *
 *  The same socket carries `rail` frames: a chat joined or left this person's rail. Those are
 *  re-dispatched as a window event (`RAIL_CHANGED_EVENT`) for the shell to read its chats again.
 */
import { FOCUS_WORKSPACE_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../platform/events";
import { navigateWithin, pathWithin } from "../platform/navigate";
import { onGatewayWSConnected, onUserFrame, parseNotice, type NoticeFrame, type UserFrame } from "../surfaces/gatewayWS";
import { readableSentence } from "./callsList";

export type Notice = NoticeFrame;
export const RAIL_CHANGED_EVENT = "vexa:terminal:rail-changed";
/** How long a toast stays, and how many stack at once. */
export const TOAST_MS = 6000;
export const TOAST_MAX = 3;

type State = { items: Notice[]; unread: number; toasts: Notice[]; loaded: boolean };
let state: State = { items: [], unread: 0, toasts: [], loaded: false };
const subs = new Set<() => void>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
let fetchImpl: typeof fetch = (...a) => fetch(...a);

function set(next: Partial<State>) {
  state = { ...state, ...next };
  subs.forEach((f) => f());
}

export function getNotifications(): State { return state; }
export function subscribeNotifications(fn: () => void): () => void { subs.add(fn); return () => { subs.delete(fn); }; }

/** Read the person's notifications (newest first). A server without the store answers 503: none. */
export async function loadNotifications(): Promise<void> {
  try {
    const r = await fetchImpl("/api/notifications?limit=30", { cache: "no-store" });
    if (!r.ok) { set({ loaded: true }); return; }
    const d = await r.json() as { notifications?: Notice[]; unread?: number };
    set({ items: Array.isArray(d.notifications) ? d.notifications : [], unread: Number(d.unread) || 0, loaded: true });
  } catch { set({ loaded: true }); }
}

function dismissToast(id: string) {
  const t = timers.get(id);
  if (t) clearTimeout(t);
  timers.delete(id);
  if (state.toasts.some((x) => x.id === id)) set({ toasts: state.toasts.filter((x) => x.id !== id) });
}
export { dismissToast };

// ── a tab in the background (founder 2026-10-14: "browser notification + sound") ─────────────
/** How the page reaches a person who is not looking at it. Injected so tests drive it; the default
 *  is the browser's own Notification API and a soft two-note chime through Web Audio. */
export type Alerts = {
  hidden: () => boolean;
  permission: () => NotificationPermission | "unsupported";
  request?: () => Promise<NotificationPermission>;
  show: (n: Notice) => void;
  chime: () => void;
};
const ASKED_KEY = "vx-notice-alerts-asked";
const browserAlerts: Alerts = {
  hidden: () => typeof document !== "undefined" && document.visibilityState === "hidden",
  permission: () => (typeof Notification === "undefined" ? "unsupported" : Notification.permission),
  request: () => (typeof Notification === "undefined" ? Promise.resolve("denied" as NotificationPermission) : Notification.requestPermission()),
  show: (n) => {
    try {
      const b = new Notification("Vexa", { body: readableSentence(n.text), tag: n.id });
      b.onclick = () => { try { window.focus(); } catch { /* */ } openNotice(n); b.close(); };
    } catch { /* a browser that refuses (no permission, an insecure origin) shows the toast only */ }
  },
  chime: () => {
    try {
      const Ctx = (window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const t = ctx.currentTime;
      // two soft notes, a fifth apart, a quarter second in all — a knock, not an alarm
      for (const [f, at] of [[660, 0], [990, 0.12]] as const) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t + at);
        g.gain.exponentialRampToValueAtTime(0.06, t + at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.18);
        o.connect(g).connect(ctx.destination);
        o.start(t + at); o.stop(t + at + 0.2);
      }
      setTimeout(() => { void ctx.close().catch(() => undefined); }, 600);
    } catch { /* no audio is fine */ }
  },
};
let alerts: Alerts = browserAlerts;
/** For tests: replace how the page alerts (null → the browser's). */
export function __setAlerts(a: Alerts | null): void { alerts = a ?? browserAlerts; }

function alertInBackground(n: Notice): void {
  try {
    if (!alerts.hidden()) return;
    if (alerts.permission() === "granted") alerts.show(n);
    if (!noticeSound) alerts.chime();
  } catch { /* an alert is a courtesy; the bell has it */ }
}

// ── the person's sounds (0.13.4 S6) ──────────────────────────────────────────────────────────
/** When installed (`./noticeSounds`), it decides the sound of every NEW notice by the person's
 *  policy (`platform/sounds.soundFor`) and the background chime above steps aside, so a notice never
 *  sounds twice. Not installed → the behaviour above, unchanged. */
export type NoticeSound = (n: Notice) => void;
let noticeSound: NoticeSound | null = null;
export function setNoticeSound(fn: NoticeSound | null): void { noticeSound = fn; }

/** May the bell offer to turn background alerts on? Once: never again after the person answered. */
export function alertsAskable(): boolean {
  if (alerts.permission() !== "default" || !alerts.request) return false;
  try { return localStorage.getItem(ASKED_KEY) !== "1"; } catch { return true; }
}
/** The person answered the offer (either way): remember it, and ask the browser if they said yes. */
export async function answerAlerts(yes: boolean): Promise<void> {
  try { localStorage.setItem(ASKED_KEY, "1"); } catch { /* */ }
  set({});
  if (yes && alerts.request) { try { await alerts.request(); } catch { /* */ } }
  set({});
}

/** A notification arrived live: it goes to the top of the list, counts as unread if it was not
 *  already, and shows as a toast for TOAST_MS (at most TOAST_MAX at once; the oldest makes room).
 *  A new one also reaches a person whose tab is in the background (browser alert + soft chime). */
export function receive(n: Notice): void {
  const had = state.items.find((x) => x.id === n.id);
  if (!had && !n.read) {
    alertInBackground(n);
    try { noticeSound?.(n); } catch { /* a sound is a courtesy */ }
  }
  const items = [n, ...state.items.filter((x) => x.id !== n.id)].slice(0, 100);
  const unread = state.unread + (had && !had.read ? 0 : n.read ? 0 : 1);
  let toasts = [n, ...state.toasts.filter((x) => x.id !== n.id)];
  for (const old of toasts.slice(TOAST_MAX)) { const t = timers.get(old.id); if (t) clearTimeout(t); timers.delete(old.id); }
  toasts = toasts.slice(0, TOAST_MAX);
  const prev = timers.get(n.id);
  if (prev) clearTimeout(prev);
  timers.set(n.id, setTimeout(() => dismissToast(n.id), TOAST_MS));
  set({ items, unread, toasts });
}

/** Put the server's copy of a row in place (an act in this tab or another). */
function replaceItem(n: Notice, unread?: number): void {
  const items = state.items.some((x) => x.id === n.id) ? state.items.map((x) => (x.id === n.id ? n : x)) : [n, ...state.items];
  set({ items, toasts: state.toasts.filter((x) => x.id !== n.id),
        ...(typeof unread === "number" ? { unread } : {}) });
  if (n.read) dismissToast(n.id);
}
/** A home frame from the socket (#1783), for the shell's open home. */
export const HOME_FRAME_EVENT = "vexa:home:frame";

export function handleFrame(f: UserFrame): void {
  if (f.type === "notification") receive(f.notification);
  else if (f.type === "notification.update") replaceItem(f.notification, f.unread);
  else if (f.type === "notification.read") {
    set({ unread: f.unread, ...(f.unread === 0 ? { items: state.items.map((x) => ({ ...x, read: true })) } : {}) });
  }
}

/** `rail` frames are the rail's live refresh, not notifications: they keep flowing when the
 *  `notifications` feature is switched off (`startRailFrames`, mounted by the rail itself). */
export function handleRailFrame(f: UserFrame): void {
  if (f.type === "rail" && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(RAIL_CHANGED_EVENT, { detail: { change: f.change, session: f.session } }));
  } else if ((f.type === "home.item" || f.type === "home.composed") && typeof window !== "undefined") {
    // the open home patches a card in place, or reads a new composition when the pointer is off it
    window.dispatchEvent(new CustomEvent(HOME_FRAME_EVENT, { detail: f }));
  }
}

let railStarted = 0;
let railStop: (() => void) | null = null;
/** Listen for `rail` frames (idempotent; reference-counted). Independent of notifications. */
export function startRailFrames(): () => void {
  railStarted += 1;
  if (railStarted === 1) railStop = onUserFrame(handleRailFrame);
  return () => {
    railStarted -= 1;
    if (railStarted === 0 && railStop) { railStop(); railStop = null; }
  };
}

/** Mark notifications read (the ids, or all). The count changes here at once; the server's answer
 *  is the truth it settles to. */
export async function markRead(ids: string[] | "all"): Promise<void> {
  const all = ids === "all";
  const want = new Set(all ? [] : ids);
  const touched = state.items.filter((x) => !x.read && (all || want.has(x.id))).length;
  if (!all && touched === 0) return;
  set({ items: state.items.map((x) => (all || want.has(x.id) ? { ...x, read: true } : x)),
        unread: all ? 0 : Math.max(0, state.unread - touched) });
  try {
    const r = await fetchImpl("/api/notifications/read", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(all ? { all: true } : { ids: [...want] }),
    });
    if (r.ok) { const d = await r.json() as { unread?: number }; if (typeof d.unread === "number") set({ unread: d.unread }); }
  } catch { /* the next load settles it */ }
}

/** Open what a notification is about, and mark it read. A chat opens in the rail (it is there:
 *  the add told the rail to read again); a workspace the person is in opens in place (it joins this
 *  chat and becomes where it writes); an invite not yet answered opens at its join link; a meeting
 *  at its share link, or by id. */
export function openNotice(n: Notice): void {
  void markRead([n.id]);
  dismissToast(n.id);
  if (typeof window === "undefined") return;
  if (n.object.kind === "chat") {
    const turn = turnOf(n.object.url);
    window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: n.object.id, ...(turn ? { turn } : {}) } }));
    return;
  }
  if (n.object.kind === "meeting") {
    if (pathWithin(n.object.url)) { navigateWithin(n.object.url); return; }
    // a meeting the agent pointed at (`notify`), or the bot's: the shell opens it like a meeting chip
    window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref: n.object.id } }));
    return;
  }
  if (n.object.kind === "none") return;
  if (n.object.kind === "workspace" && (n.state === "accepted" || n.kind === "workspace_added" || n.kind === "invite_accepted")) {
    focusWorkspace(n.object.id, n.object.name);
    return;
  }
  if (n.object.url) { navigateWithin(n.object.url); return; }
  if (n.object.kind === "workspace") navigateWithin(`/w/${encodeURIComponent(n.object.id)}`);
}

function focusWorkspace(id: string, name?: string): void {
  window.dispatchEvent(new CustomEvent(FOCUS_WORKSPACE_EVENT, { detail: { workspace: id, ...(name ? { name } : {}) } }));
}

function turnOf(url?: string): string {
  if (!url) return "";
  try { return new URL(url, "http://x").searchParams.get("turn") || ""; } catch { return ""; }
}

// ── one tap ─────────────────────────────────────────────────────────────────────────────────
export type Act = { action: "accept" | "decline" | "join" | "open"; label: string };
const OPEN_LABEL: Record<string, string> = {
  meeting_shared: "Open transcript", chat_mentioned: "Open chat", chat_added: "Open chat", chat_shared: "Open chat",
  routine_posted: "Open chat", agent: "Open",
};
/** The one act a notice puts first: Accept an invite, Join a call, otherwise Open its object —
 *  or nothing when there is nothing to open (a bot set for a meeting that does not exist yet). */
/** The acts a row offers. A row from an older server carries none: an invite with its link still
 *  offers Accept and Decline (through the invite's own doors), anything else Open. */
function actsOf(n: Notice): string[] {
  if (n.actions) return n.actions;
  if (n.kind === "workspace_invited") return inviteToken(n) ? ["accept", "decline", "open"] : [];
  return n.object.kind === "none" ? [] : ["open"];
}
export function primaryAction(n: Notice): Act | null {
  const acts = actsOf(n);
  if (acts.includes("accept")) return { action: "accept", label: "Accept" };
  if (acts.includes("join")) return { action: "join", label: "Join" };
  if (acts.includes("open") && n.object.kind !== "none") return { action: "open", label: OPEN_LABEL[n.kind] ?? "Open" };
  return null;
}
/** The second act, when there is one: Decline an invite or a call. */
export function secondaryAction(n: Notice): Act | null {
  return actsOf(n).includes("decline") ? { action: "decline", label: "Decline" } : null;
}
/** What the person already did with it, in words ("Joined", "Declined"), or "". */
export function stateLabel(n: Notice): string {
  return n.state === "accepted" || n.state === "joined" ? "Joined" : n.state === "declined" ? "Declined" : "";
}

/** Do one of a notice's acts, as the person, in one tap. `open` is the client's own navigation
 *  (and marks it read); accept, decline and join go to the server's one door for them
 *  (`POST /api/notifications/act`, the same act as the join page), which marks the row read and
 *  answers what to open next. A server without that door falls back to the join page. */
export async function actOn(n: Notice, action: Act["action"]): Promise<boolean> {
  if (action === "open") { openNotice(n); return true; }
  dismissToast(n.id);
  let r: Response | null = null;
  try {
    r = await fetchImpl("/api/notifications/act", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: n.id, action }),
    });
  } catch { r = null; }
  if (!r || !r.ok) {
    if (r && r.status !== 404 && r.status !== 405 && r.status !== 503) return false;
    // an older server: the invite's own doors
    if (action === "decline") return declineInvite(n);
    if (action === "accept" && n.object.url && typeof window !== "undefined") { navigateWithin(n.object.url); return true; }
    return false;
  }
  const d = await r.json().catch(() => ({})) as { notification?: unknown; open?: { kind?: string; id?: string; name?: string; url?: string; turn?: string } | null };
  const row = parseNotice(d.notification) ?? { ...n, read: true };
  const wasUnread = !(state.items.find((x) => x.id === n.id)?.read ?? n.read);
  replaceItem(row, wasUnread ? Math.max(0, state.unread - 1) : undefined);
  const o = d.open;
  if (o && typeof window !== "undefined") {
    if (o.kind === "workspace" && o.id) focusWorkspace(o.id, o.name);
    else if ((o.kind === "chat" || o.kind === "call") && o.id)
      window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: o.id, ...(o.turn ? { turn: o.turn } : {}), ...(o.kind === "call" ? { call: true } : {}) } }));
    else if (o.kind === "meeting" && o.id) {
      if (pathWithin(o.url)) navigateWithin(o.url);
      else window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref: o.id } }));
    }
  }
  return true;
}

// ── the list ────────────────────────────────────────────────────────────────────────────────
/** Unread first, then newest first within each. */
export function sortForList(items: Notice[]): Notice[] {
  return [...items].sort((a, b) => (Number(a.read) - Number(b.read)) || (b.created - a.created));
}
/** "just now", "5 min ago", "2 h ago", "yesterday", then a date. */
export function relTime(ms: number, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 2 * 86400) return "yesterday";
  try {
    const d = new Date(ms);
    return d.getFullYear() === new Date(now).getFullYear()
      ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
      : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });
  } catch { return ""; }
}

/** The token an invite notice's join link carries (`/join?i=<token>`), or "". */
export function inviteToken(n: Notice): string {
  if (n.kind !== "workspace_invited" || !n.object.url) return "";
  try { return new URL(n.object.url, "http://x").searchParams.get("i") || ""; } catch { return ""; }
}

/** Decline an invite made for this person: the invite stops working and its sender is told.
 *  Resolves to whether the server took it. */
export async function declineInvite(n: Notice): Promise<boolean> {
  const token = inviteToken(n);
  if (!token) return false;
  try {
    const r = await fetchImpl("/api/workspace/invites/decline", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
    });
    if (!r.ok) return false;
  } catch { return false; }
  set({ items: state.items.map((x) => (x.id === n.id ? { ...x, read: true, object: { ...x.object, url: undefined } } : x)) });
  void markRead([n.id]);
  return true;
}

let started = 0;
let stop: (() => void) | null = null;
/** Start listening (idempotent; reference-counted by the components that mount it). */
export function startNotifications(): () => void {
  started += 1;
  if (started === 1) {
    void loadNotifications();
    const offFrame = onUserFrame(handleFrame);
    let first = true;
    // a reconnect may have missed frames: read the list again (not on the first open)
    const offConn = onGatewayWSConnected((up) => { if (up && !first) void loadNotifications(); if (up) first = false; });
    stop = () => { offFrame(); offConn(); };
  }
  return () => {
    started -= 1;
    if (started === 0 && stop) { stop(); stop = null; }
  };
}

/** For tests: a fetch double and a clean store. */
export function __resetNotifications(f?: typeof fetch): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  state = { items: [], unread: 0, toasts: [], loaded: false };
  if (f) fetchImpl = f;
  subs.forEach((fn) => fn());
}
