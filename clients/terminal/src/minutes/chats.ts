/** CHATS — the rail's ONE object, and the only thing the left pane lists.
 *
 *  A chat IS the saved focus state: `{ id, label, meeting?, workspaces[], artifacts[] }`. A deeplink
 *  (`?ask=`, `?view=`, `?meeting=`) is a chat CONSTRUCTOR, not a route — it mints one of these and
 *  the state belongs to the chat from then on. Meetings are not a separate kind: a meeting's chat
 *  carries its `meeting` ref, and that ref is what makes the room render the meeting layout.
 *
 *  PROJECTS are gone as a rail concept (founder ruling): the workspace SET a chat is over moved onto
 *  the chat itself, so there is nothing left to nest under. `migrateProjects()` below flattens the
 *  old registry once — every project chat becomes a flat chat inheriting its project's `set`.
 *
 *  Everything here is a pure function on purpose (the shell wires them, the tests read them
 *  directly) except the two localStorage seams at the bottom.
 */
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";

/** One open document in the right panel — a TAB. Identical in shape to `Page`, and deliberately so:
 *  the panel's tab strip and the chat's saved artifacts are the same list, not two lists kept in
 *  step. `label` is carried rather than recomputed because a phase page's name ("Minutes" vs
 *  "Brief") is a property of the room that produced it, not of the path. */
export type Artifact = { kind?: "doc" | "meeting"; path: string; slug?: string; label: string };

/** A tab's identity. Path alone is not enough — `README.md` exists in every workspace.
 *
 *  A `meeting` tab is keyed on a namespace no workspace slug can occupy, so a DOC tab's key is
 *  byte-for-byte what it always was: the `focus` values already persisted in every chat record keep
 *  resolving, which is the whole reason `kind` is optional rather than defaulted to a string. */
export const artifactKey = (a: { kind?: string; path: string; slug?: string }) =>
  a.kind === "meeting" ? `@meeting|${a.path}` : `${a.slug ?? ""}|${a.path}`;

/** The record. `touched` is the whole filter: it is written at SEND time and at explicit-create
 *  time, never derived by fetching a history — an untouched auto-created chat is exactly the thing
 *  the default filter hides.
 *
 *  `artifacts` + `focus` are the human's reading state, and they are saved HERE rather than in the
 *  panel: opening, switching and closing a tab is a fact about the conversation, so reopening the
 *  chat restores its documents and the agent's context bundle can name what is in front of you. */
export type Chat = {
  id: string;                 // also the agent session id — `meet-<meetingId>` for a meeting's chat
  label: string;
  meeting?: string;           // the meeting row id this chat is about (string form of MeetingMock.id)
  workspaces: string[];       // the mount set — what a PROJECT used to own
  artifacts: Artifact[];      // the open tabs (seeded by the room's phase pages and by `?view=`)
  focus?: string;             // artifactKey() of the tab in front
  touched?: boolean;          // a user wrote in it, or a user made it by hand
  createdAt: number;
  lastActivityAt: number;
};

export const CHATS_KEY = "vexa.minutes.chats";
/** The legacy project registry. Read ONCE, on the migration path, and never written again —
 *  it stays on disk untouched as the backup. */
export const PROJECTS_KEY = "vexa.minutes.projects";
export const RAIL_ALL_KEY = "vexa.minutes.railAll";

/** WHOSE rail this is.
 *
 *  `CHATS_KEY` is one global key, so a second identity signing in on the same browser INHERITED
 *  the previous person's rows — including chats for meetings they cannot see. Reproduced
 *  2026-09-02: a brand-new account's rail opened showing another account's meeting.
 *  `onboardingState.ts` had already learned this ("keyed by the user's identity so switching users
 *  is clean"); the rail simply never did.
 *
 *  The owner is a SEPARATE key rather than a wrapper around the payload, because the payload shape
 *  is load-bearing for the legacy migration below and a stamp does not need to touch it. The client
 *  cannot know its identity synchronously — `vexa-user-info` is httpOnly and `/api/auth/me` is a
 *  fetch — so the rail loads first and is CHECKED when the identity arrives. Same person: nothing
 *  happens, no flicker. Different person: the rail is theirs, not this reader's, and it goes.
 *
 *  One-time caveat, deliberate: a rail stored before this key existed has no owner, so the first
 *  identity to observe it ADOPTS it rather than losing it. Wiping every existing rail on upgrade
 *  would be a worse trade than one inheritance on a browser that was already shared. */
export const RAIL_OWNER_KEY = "vexa.minutes.chatsOwner";

/** The identity that owns the stored rail, or null when nothing has claimed it yet. */
export function readRailOwner(): string | null {
  try { return localStorage.getItem(RAIL_OWNER_KEY); } catch { return null; }
}

export function writeRailOwner(identity: string): void {
  try { localStorage.setItem(RAIL_OWNER_KEY, identity); } catch { /* ignore */ }
}

/** Drop the stored rail and hand back a fresh one (seeds only) — used when the reader is not the
 *  person whose rail is in storage. Nothing is lost that matters: meeting rows are DERIVED from the
 *  meetings list and come back on their own, and every chat's session lives on the server. */
export function resetChats(now = Date.now()): Chat[] {
  try { localStorage.removeItem(CHATS_KEY); } catch { /* ignore */ }
  const fresh = ensureSeeds([], now);
  saveChats(fresh);
  return fresh;
}
/** Which side columns the reader has folded away. One key per side, because the two are independent
 *  choices and a combined key would make forgetting one of them the default. */
export const COLLAPSED_KEY = { left: "vexa.minutes.railCollapsed", right: "vexa.minutes.pagesCollapsed" } as const;
export type Side = keyof typeof COLLAPSED_KEY;

export const ORG_CHAT_ID = "org-setup";
export const PERSONAL_CHAT_ID = "main";
export const ORG_CHAT_LABEL = "Organisation setup";

/** A meeting's own chat has a DERIVED id, so materialising the row lands on the same agent session
 *  the shell has always used for that meeting (`meet-<id>`). Second and later chats on the same
 *  meeting get ordinary minted ids. */
export const meetingChatId = (meetingId: string | number) => `meet-${meetingId}`;

export const meetingTitle = (m: MeetingMock) => String(m.title ?? "").split(" — ")[0] || "Meeting";

/** When a meeting sits on the timeline. 0 = unknown, which sorts last. */
export function meetingWhen(m: MeetingMock | undefined): number {
  const t = (m as { start_time?: string } | undefined)?.start_time;
  if (!t) return 0;
  const n = Date.parse(t);
  return Number.isFinite(n) ? n : 0;
}

/** The rail's SHORT time label. Same vocabulary the meeting rows always used — weekday, day+month,
 *  a clock — but only ever one of them: the rail is 248px wide and a full "Tue, Sep 1 07:04 PM"
 *  left about 90px for the name, which truncated "Organisation setup" to "Organi…". Coarser the
 *  further away it is: a clock today, a weekday this week, a date beyond that. */
export function whenShort(ms: number, opts: { live?: boolean; now?: number } = {}): string {
  if (opts.live) return "live";
  if (!ms) return "";
  try {
    const now = opts.now ?? Date.now();
    const d = new Date(ms), n = new Date(now);
    if (d.toDateString() === n.toDateString()) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (Math.abs(ms - now) < 6 * 86400000) return d.toLocaleDateString(undefined, { weekday: "short" });
    return d.getFullYear() === n.getFullYear()
      ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
      : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });
  } catch { return ""; }
}

// ── the union list ───────────────────────────────────────────────────────────────────

/** One rail line. `chatId === null` = a meeting nobody has opened yet: the row is DERIVED from the
 *  meeting and materialises its chat on first open. */
export type Row = {
  key: string;
  chatId: string | null;
  meetingId: string | null;
  label: string;
  when: number;
  whenLabel: string;
  live: boolean;
  upcoming: boolean;
  touched: boolean;
  workspaces: string[];
};

/** Stored chats UNION live meetings-as-rows.
 *
 *  A meeting with no chat yet still shows (derived). A meeting with two chats shows twice — the chat
 *  is the row, not the meeting. Order is recency, `max(last activity, meeting start)`, newest first,
 *  with ONE lift: a meeting that is running right now goes to the top. (The founder's rule names the
 *  formula and its consequence — "live meetings naturally top" — and the formula alone does not
 *  produce it, because an upcoming meeting's start_time is in the FUTURE and would outrank a live
 *  one. The live lift is what makes the stated consequence true.) No buckets: this is one flat list.
 *
 *  Sorting and LABELLING part company on one point: a meeting row is labelled with the MEETING's own
 *  time, never the chat's last activity, because "Blue Light Card · today" would be a plain lie about
 *  a meeting held on Monday. Reading a row is not the meeting moving. */
export function railRows(chats: Chat[], meetings: MeetingMock[], now = Date.now()): Row[] {
  const byId = new Map<string, MeetingMock>();
  for (const m of meetings) byId.set(String(m.id), m);
  const claimed = new Set<string>();
  const rows: Row[] = [];

  for (const c of chats) {
    const m = c.meeting ? byId.get(c.meeting) : undefined;
    if (c.meeting) claimed.add(c.meeting);
    const phase = m ? meetingPhase(m) : null;
    const live = phase === "live";
    const upcoming = phase === "prep";
    const when = Math.max(c.lastActivityAt || 0, meetingWhen(m));
    rows.push({
      key: `c:${c.id}`,
      chatId: c.id,
      meetingId: c.meeting ?? null,
      label: c.label || (m ? meetingTitle(m) : "Chat"),
      when,
      whenLabel: whenShort(m ? meetingWhen(m) : when, { live, now }),
      live, upcoming,
      touched: !!c.touched,
      workspaces: c.workspaces,
    });
  }

  for (const m of meetings) {
    const id = String(m.id);
    if (claimed.has(id)) continue;
    const phase = meetingPhase(m);
    const when = meetingWhen(m);
    rows.push({
      key: `m:${id}`,
      chatId: null,
      meetingId: id,
      label: meetingTitle(m),
      when,
      whenLabel: whenShort(when, { live: phase === "live", now }),
      live: phase === "live",
      upcoming: phase === "prep",
      touched: false,
      workspaces: ["personal", "_global"],
    });
  }

  return rows.sort((a, b) =>
    (b.live ? 1 : 0) - (a.live ? 1 : 0)
    || b.when - a.when
    || a.label.localeCompare(b.label));
}

/** The one chip. Default shows what deserves attention — chats the user TOUCHED, plus meetings that
 *  are live or still ahead. `all` shows everything, which is where the never-touched auto-created
 *  chats (email deeplinks, presets, flows) and the untouched archive live.
 *
 *  `keep` is the selected row: whatever the filter says, the row you are reading never vanishes
 *  under you — opening an untouched meeting materialises an untouched chat, and without this the
 *  row would disappear the moment it was selected. */
export function visibleRows(rows: Row[], all: boolean, keep?: string | null): Row[] {
  if (all) return rows;
  return rows.filter((r) => r.touched || r.live || r.upcoming || (keep != null && r.key === keep));
}

// ── mutations (pure: array in, array out) ────────────────────────────────────────────

export function markTouched(chats: Chat[], chatId: string, now = Date.now()): Chat[] {
  let hit = false;
  const next = chats.map((c) => {
    if (c.id !== chatId) return c;
    hit = true;
    return { ...c, touched: true, lastActivityAt: now };
  });
  return hit ? next : chats;
}

export function upsertChat(chats: Chat[], chat: Chat): Chat[] {
  return chats.some((c) => c.id === chat.id)
    ? chats.map((c) => (c.id === chat.id ? { ...c, ...chat } : c))
    : [...chats, chat];
}

export function removeChat(chats: Chat[], chatId: string): Chat[] {
  return chats.filter((c) => c.id !== chatId);
}

/** The chat behind a row, minting it if the row was derived from a meeting. Opening is NOT touching
 *  (the founder's definition is a user-authored message or an explicit create), so the materialised
 *  record starts untouched. */
export function chatForRow(chats: Chat[], row: Row, meetings: MeetingMock[], now = Date.now()): Chat {
  if (row.chatId) {
    const found = chats.find((c) => c.id === row.chatId);
    if (found) return found;
  }
  const m = row.meetingId ? meetings.find((x) => String(x.id) === row.meetingId) : undefined;
  const id = row.meetingId ? meetingChatId(row.meetingId) : `pchat-${now.toString(36)}`;
  return {
    id,
    label: m ? meetingTitle(m) : row.label,
    meeting: row.meetingId ?? undefined,
    workspaces: ["personal", "_global"],
    artifacts: [],
    touched: false,
    createdAt: now,
    lastActivityAt: Math.max(row.when, now),
  };
}

export function newChat(label: string, workspaces: string[], opts: { id?: string; touched?: boolean; meeting?: string; now?: number } = {}): Chat {
  const now = opts.now ?? Date.now();
  return {
    id: opts.id ?? `pchat-${now.toString(36)}`,
    label,
    meeting: opts.meeting,
    workspaces,
    artifacts: [],
    touched: opts.touched ?? true,
    createdAt: now,
    lastActivityAt: now,
  };
}

// ── migration ────────────────────────────────────────────────────────────────────────

export type LegacyProject = {
  id: string;
  name?: string;
  set?: string[];
  builtin?: "personal" | "org";
  chats?: { id: string; label: string }[];
};

const isOrgProject = (p: LegacyProject) => p.builtin === "org" || p.id === "org";
const isPersonalProject = (p: LegacyProject) => p.builtin === "personal" || p.id === "personal";

/** A flat chat's label from a project chat's. The project name was the outer row; flattening it away
 *  would leave three chats all called "setup", so the name comes along as a qualifier — except in
 *  Personal (where the chat label already stood alone) and in the org project (whose setup chat has
 *  the name the founder gave it). */
function flatLabel(p: LegacyProject, label: string): string {
  const chat = (label || "chat").trim();
  if (isPersonalProject(p)) return chat;
  const name = (p.name || p.id || "Project").trim();
  if (isOrgProject(p)) return /^setup$/i.test(chat) ? ORG_CHAT_LABEL : `Organisation · ${chat}`;
  if (chat.toLowerCase() === name.toLowerCase()) return chat;
  return `${name} · ${chat}`;
}

/** ONE-WAY: old registry in, flat chats out. Every project's chats become flat chats inheriting the
 *  project's `set` as their `workspaces[]`; the project itself does not survive. Migrated chats are
 *  TOUCHED — they were somebody's real work, and the old UI could not tell an auto-created chat from
 *  a hand-made one, so the safe reading is "keep it visible".
 *
 *  Timestamps run `now - i` so the rail's newest-first order reproduces the registry's own order. */
export function migrateProjects(projects: LegacyProject[], now = Date.now()): Chat[] {
  const out: Chat[] = [];
  let i = 0;
  for (const p of projects ?? []) {
    const set = (p.set && p.set.length ? p.set : isOrgProject(p) ? ["_global"] : ["personal", "_global"]).slice();
    if (isPersonalProject(p)) {
      // the personal project's "main" row was BUILT IN — it never appeared in `chats[]`, so it has
      // to be reconstructed here or the user's oldest conversation would vanish in the flattening.
      out.push({ id: PERSONAL_CHAT_ID, label: "Personal", workspaces: set, artifacts: [], touched: true, createdAt: now - i, lastActivityAt: now - i });
      i++;
    }
    for (const c of p.chats ?? []) {
      if (!c || !c.id) continue;
      out.push({
        id: c.id,
        label: flatLabel(p, c.label),
        workspaces: set,
        artifacts: [],
        touched: true,
        createdAt: now - i,
        lastActivityAt: now - i,
      });
      i++;
    }
  }
  return dedupe(out);
}

function dedupe(chats: Chat[]): Chat[] {
  const seen = new Set<string>();
  const out: Chat[] = [];
  for (const c of chats) {
    if (seen.has(c.id)) continue;
    seen.add(c.id);
    out.push(c);
  }
  return out;
}

/** THE COMPANY-LAYER HINT — a render-time cache, never the authority.
 *
 *  `loadChats` is synchronous (it reads localStorage during the first render) and the gate lives on
 *  the server, so the rail cannot await a probe before deciding what rows exist. SetupGate — which
 *  polls `/api/global/state` anyway — writes the answer here, and this reads it.
 *
 *  It is a HINT and the distinction is load-bearing: the server refuses every gated request on its
 *  own (agent-api 403s a non-admin on a gated instance, the flows engine parks, the operator verbs
 *  refuse), so being wrong here costs a row in a list, never access to anything. It therefore FAILS
 *  OPEN — an unwritten or unreadable hint seeds the normal rail, because the cost of guessing
 *  "missing" wrongly is hiding a real user's own chat from them. */
const LAYER_HINT_KEY = "vexa.companyLayer.v1";

export function setCompanyLayerHint(state: "missing" | "completed"): void {
  try { localStorage.setItem(LAYER_HINT_KEY, state); } catch { /* locked-down storage */ }
}

/** THREE-valued, and the third value is the whole correction.
 *
 *  ⚠ The first version read `=== "missing"` and treated everything else — including an ABSENT
 *  hint — as "the layer is fine, seed the rows". That defeats itself on the exact case it exists
 *  for: a first admin on a fresh browser has no hint yet, because the poll that writes it has not
 *  returned when the rail renders. Verified live on a cleared browser: the admin got the Personal
 *  row, the generic "paste a meeting link" greeting and the personal README template — the
 *  founder's original complaint, reproduced by the fix meant to prevent it.
 *
 *  So `null` (unknown) is its own answer and it does NOT seed. The rows are restored by the
 *  re-seed below the moment the probe says "completed", which is under a second later — and they
 *  are derived, not stored, so nothing has to be un-hidden. Being briefly rowless costs a second;
 *  guessing wrong the other way costs the first impression this whole gate exists to protect. */
export function companyLayerHint(): "missing" | "completed" | null {
  try {
    const v = localStorage.getItem(LAYER_HINT_KEY);
    return v === "missing" || v === "completed" ? v : null;
  } catch { return null; }
}

/** The two rows that must always be reachable: your own chat, and the `_global` admin setup — which
 *  after the flattening is just another chat row. Both are structural, never spam, so both count as
 *  touched (an untouched org-setup row would hide behind the filter and take admin with it).
 *
 *  ── EXCEPT BEFORE THE INSTANCE IS SET UP (founder ruling 2026-09-02) ──────────────────────────
 *  On a fresh instance the admin is the only person who can be here, and the only thing that can
 *  usefully happen is writing the company layer. The founder clicked through his own first claim
 *  and got a "Personal" row seeded on the generic greeting — "paste a meeting link" — beside a
 *  second "Organisation setup" row, on an instance that could not join a meeting or send a mail:
 *  "this is what I get from the first admin click — it should want to setup global here."
 *  So while the layer is missing there are NO seed rows. The setup conversation the preset opens is
 *  the only chat, and it is the whole screen. They come back the moment the instance opens — these
 *  rows are derived, not stored, so nothing has to be un-hidden later. */
export function seedChats(now = Date.now()): Chat[] {
  if (companyLayerHint() !== "completed") return [];
  return [
    { id: PERSONAL_CHAT_ID, label: "Personal", workspaces: ["personal", "_global"], artifacts: [], touched: true, createdAt: now, lastActivityAt: now },
    { id: ORG_CHAT_ID, label: ORG_CHAT_LABEL, workspaces: ["_global"], artifacts: [], touched: true, createdAt: now - 1, lastActivityAt: now - 1 },
  ];
}

/** Whatever we load, these two exist — unless the instance has not been set up yet. */
export function ensureSeeds(chats: Chat[], now = Date.now()): Chat[] {
  const out = [...chats];
  for (const s of seedChats(now)) if (!out.some((c) => c.id === s.id)) out.push(s);
  return out;
}

function normalise(raw: unknown, now: number): Chat[] {
  if (!Array.isArray(raw)) return [];
  const out: Chat[] = [];
  for (const r of raw as Partial<Chat>[]) {
    if (!r || typeof r.id !== "string" || !r.id) continue;
    out.push({
      id: r.id,
      // A MEETING-BOUND chat may carry no label of its own, and that is not missing data: railRows
      // names it from the meeting (`c.label || meetingTitle(m)`), so the row follows the meeting's
      // title instead of freezing whatever it was called when it was created. Defaulting those to
      // "Chat" here is what made the empty label a one-render trick — it survived in memory and
      // was rewritten to "Chat" on the next load. Only a chat with NO meeting needs a fallback.
      label: typeof r.label === "string" && r.label ? r.label : (typeof r.meeting === "string" && r.meeting ? "" : "Chat"),
      meeting: typeof r.meeting === "string" && r.meeting ? r.meeting : undefined,
      workspaces: Array.isArray(r.workspaces) && r.workspaces.length ? r.workspaces.filter((w) => typeof w === "string") : ["personal", "_global"],
      // tolerant on purpose: an early build stored artifacts as bare path strings, and a stored tab
      // whose shape we no longer understand is dropped rather than allowed to render as nothing.
      artifacts: Array.isArray(r.artifacts)
        ? (r.artifacts as unknown[])
            .filter((a): a is Artifact =>
              !!a && typeof a === "object" && typeof (a as Artifact).path === "string" && typeof (a as Artifact).label === "string")
            // a `kind` we do not understand degrades to a document rather than to a tab nothing
            // can render — the same tolerance the artifact list has always had for shape drift.
            .map((a) => ({ ...a, kind: a.kind === "meeting" ? ("meeting" as const) : undefined }))
        : [],
      focus: typeof r.focus === "string" && r.focus ? r.focus : undefined,
      touched: !!r.touched,
      createdAt: Number.isFinite(r.createdAt) ? Number(r.createdAt) : now,
      lastActivityAt: Number.isFinite(r.lastActivityAt) ? Number(r.lastActivityAt) : Number(r.createdAt) || now,
    });
  }
  return dedupe(out);
}

// ── storage ──────────────────────────────────────────────────────────────────────────

/** Read the flat chat list, migrating the old project registry exactly once.
 *
 *  The migration trigger is the ABSENCE of the new key — which makes it one-way by construction,
 *  with no second marker to keep in sync. The old key is never written and never removed: it is the
 *  backup, and a user who empties their chat list does not get their old projects resurrected. */
export function loadChats(now = Date.now()): Chat[] {
  let stored: string | null = null;
  try { stored = localStorage.getItem(CHATS_KEY); } catch { /* locked-down storage */ }
  if (stored != null) {
    let parsed: unknown = null;
    try { parsed = JSON.parse(stored); } catch { /* corrupt → fall through to seeds */ }
    return ensureSeeds(normalise(parsed, now), now);
  }
  let legacy: LegacyProject[] = [];
  try { legacy = JSON.parse(localStorage.getItem(PROJECTS_KEY) || "[]") as LegacyProject[]; } catch { legacy = []; }
  const migrated = ensureSeeds(migrateProjects(Array.isArray(legacy) ? legacy : [], now), now);
  saveChats(migrated);
  return migrated;
}

export function saveChats(chats: Chat[]): void {
  try { localStorage.setItem(CHATS_KEY, JSON.stringify(chats)); } catch { /* ignore */ }
}

export function loadRailAll(): boolean {
  try { return localStorage.getItem(RAIL_ALL_KEY) === "1"; } catch { return false; }
}

export function saveRailAll(all: boolean): void {
  try { localStorage.setItem(RAIL_ALL_KEY, all ? "1" : "0"); } catch { /* ignore */ }
}

/** Collapsed is the stored EXCEPTION: anything but an explicit "1" means the column is open, so a
 *  missing key, a cleared profile or locked-down storage all land on the full three-column shell
 *  rather than on a surface with two sides missing and no memory of why. */
export function loadCollapsed(side: Side): boolean {
  try { return localStorage.getItem(COLLAPSED_KEY[side]) === "1"; } catch { return false; }
}

export function saveCollapsed(side: Side, collapsed: boolean): void {
  try { localStorage.setItem(COLLAPSED_KEY[side], collapsed ? "1" : "0"); } catch { /* ignore */ }
}
