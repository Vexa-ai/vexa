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
import { meetingPhase, type MeetingMock, type MeetingPhase } from "../surfaces/meetingModel";

/** One open document in the right panel — a TAB. Identical in shape to `Page`, and deliberately so:
 *  the panel's tab strip and the chat's saved artifacts are the same list, not two lists kept in
 *  step. `label` is carried rather than recomputed because a phase page's name ("Minutes" vs
 *  "Brief") is a property of the room that produced it, not of the path. */
export type Artifact = {
  kind?: "doc" | "meeting"; path: string; slug?: string; label: string;
  /** WHEN THIS PAGE WAS LAST IN FRONT — the strip is a history bar, ordered by it (decision 28,
   *  founder amendment: *"ensure these tabs are sorted left to right based on last used — as a
   *  history bar"*). Absent on a record written before the amendment; the migration stamps one. */
  at?: number;
  /** THE READER'S OWN DESK — the strip's first entry, on every chat (decision 26.4 / 28.5).
   *
   *  A product DEFAULT, not a pin: nobody asked for it and nobody can `×` it away. It is where the
   *  view starts when a scaffold names no focus, because a chat that opens on nothing is a chat
   *  that opens on a blank panel. Ordering puts it left of the pins. */
  desk?: boolean;
  /** THIS TAB WAS ASKED FOR (PRD decision 28). A tab exists only because somebody requested it —
   *  a pin, an explicit open-in-tab, or a scaffold declaring it at open. Navigation does not mint
   *  one. Unpinned entries are the pre-28 accumulation and the migration in `normalise` removes
   *  them; nothing writes an unpinned artifact any more. */
  pinned?: boolean;
  /** THE MEETING'S OWN PAGE, IN A MEETING CHAT — a tab that cannot be closed (Vexa-ai/vexa#1600).
   *
   *  A pin is the reader's; this is the meeting's. `forgetHistory`, `togglePinned` and the preview
   *  cap all refuse it, so no route the strip has — the `×`, an unpin, an eviction — can take the
   *  transcript off a meeting chat. See `Page.permanent` for the ruling it comes from. */
  permanent?: boolean;
};

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
  /** THE ONE OF THEM WRITES GO TO (Vexa-ai/vexa#1611).
   *
   *  Founder, 2026-09-06, in a chat whose header chip read `personal` while the whole conversation
   *  was about a customer's workspace, and whose files landed on his desk: *"it creates files in
   *  the wrong workspace, we need so that the thing knew the workspace of writing, if it's
   *  specified. We have this "personal" and we probably should be able to set a workspace that we
   *  are targeting (other workspaces still available to read and even to write, if explicit ask
   *  and purpose)"*.
   *
   *  `workspaces` is REACH; this is WHERE THE WORK LANDS, and they are different questions — a
   *  chat reads four places and writes one. Absent means the person's own desk, which is the
   *  default rather than a second name for it: a chat that has never chosen still writes somewhere,
   *  and every stored record predates the field.
   *
   *  A workspace CREATED OR JOINED from the chat becomes it (Vexa-ai/vexa#1603, and the server's
   *  `focus` event carries both halves); the person sets it by clicking a chip; the agent sets it
   *  when they say so and says it did. Every route writes through `setTarget` below. */
  target?: string;
  artifacts: Artifact[];      // the PINNED tabs — only what was asked for (decision 28)
  focus?: string;             // artifactKey() of the tab in front, when the view IS a tab
  /** THE ONE VIEW SLOT (PRD decision 28, founder: *"tab is only when tab is specifically
   *  requested"*).
   *
   *  What the panel is showing right now. Every navigation REPLACES it — an entity chip, a
   *  wikilink, a navigator file, a `/w/<id>/<path>` URL, an agent's `artifact` event with
   *  `focus: true`. None of them appends to `artifacts`.
   *
   *  Persisted beside the tabs because it is reading state like they are: reopening a chat should
   *  put back the document you were looking at, whether or not you had pinned it. Before this, the
   *  only way the panel could remember a document was to make it a tab — which is precisely how a
   *  few chip clicks became seven of them. */
  view?: Artifact;
  /** THE SCAFFOLD THIS CHAT WAS COMPOSED FROM — the kind AND the record's id, in ONE field.
   *
   *  It is what the chat IS, and things that depend on that — the header's flavour, today — read it
   *  instead of inferring. The old inference was mount arithmetic: "no workspace besides `_global`
   *  means admin", which broke the moment the setup conversation legitimately mounted the admin's
   *  own desk as well (the two-scaffold ruling). A chat's nature is not a function of how many
   *  folders it happens to have open.
   *
   *  ONE object rather than two optional fields, and that is the whole of F37 (founder, 2026-09-02:
   *  *"I explain this as stale code"*). He was looking at an `admin-setup`-flavoured row that had NO
   *  scaffold record behind it — a PLANTED row carrying the admin kind — so the render fell through
   *  to the pre-scaffold branch and offered him a research step that does not exist. Pairing the id
   *  with the kind makes that shape impossible to WRITE, not merely never rendered: there is no way
   *  to say "admin-setup" without naming the record it came from. `normalise` drops a half-record
   *  for the same reason.
   *
   *  Persisted, so it survives a reload. Without that the flavour would be right on the turn the
   *  scaffold opened the chat and wrong on every load after it — the sort of half-fix that reads as
   *  fixed. */
  scaffold?: { kind: string; id: string };
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

/** Drop the stored rail and hand back an EMPTY one — used when the reader is not the person whose
 *  rail is in storage. Empty, not "the seeds", because the rail plants nothing at all now (F34):
 *  a reader who has opened no chat has no chats. Nothing is lost that matters — meeting rows are
 *  DERIVED from the meetings list and come back on their own, and every chat's session lives on
 *  the server. */
export function resetChats(): Chat[] {
  try { localStorage.removeItem(CHATS_KEY); } catch { /* ignore */ }
  // …AND THE DELETES GO WITH IT. The tombstone list (`RAIL_HIDDEN_KEY`) is keyed to nobody, so a
  // second identity inheriting it would have the previous person's deletions silently applied to
  // THEIR server sessions — rows they never touched, missing from their rail, with no way to tell.
  // Same reasoning as the owner key itself; it simply has to be remembered in both places.
  try { localStorage.removeItem(RAIL_HIDDEN_KEY); } catch { /* ignore */ }
  saveChats([]);
  return [];
}
/** Which side columns the reader has folded away. One key per side, because the two are independent
 *  choices and a combined key would make forgetting one of them the default. */
export const COLLAPSED_KEY = { left: "vexa.minutes.railCollapsed", right: "vexa.minutes.pagesCollapsed" } as const;
export type Side = keyof typeof COLLAPSED_KEY;

/** THE TWO IDS THE RAIL USED TO PLANT. Nothing constructs them any longer — they are exported for
 *  exactly one reason, which is to be named by `pruneStale` below, whose whole job is removing the
 *  rows they identify from readers who already have them. */
export const ORG_CHAT_ID = "org-setup";
export const PERSONAL_CHAT_ID = "main";

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
  /** THE MEETING'S STATUS, ON THE CHAT'S OWN ROW (Vexa-ai/vexa#1597, founder: *"the chat itself
   *  should be Live … just attach the status to it"*). `live` while the bot is in the room, `held`
   *  once it is over, and null for a chat about no meeting or a meeting still ahead — an upcoming
   *  meeting's row already says when it is, which is the thing worth knowing about one. */
  status: "live" | "held" | null;
  touched: boolean;
  workspaces: string[];
  /** WHERE THIS ROW'S CHAT WRITES (Vexa-ai/vexa#1611) — a slug, or absent for the person's desk.
   *  On the ROW because the rail shows it: a reader scanning their conversations should be able to
   *  see which one is working in a customer's workspace without opening it. */
  target?: string;
};

/** The row's status word from the meeting's phase. `prep` deliberately has none — see `Row.status`. */
const statusOf = (phase: MeetingPhase | null): Row["status"] =>
  phase === "live" ? "live" : phase === "post" ? "held" : null;

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
 *  a meeting held on Monday. Reading a row is not the meeting moving.
 *
 *  ⚠ `claimed` IS THE WHOLE DEDUP, and Vexa-ai/vexa#1597 is what made it fire for the case it was
 *  written for. A chat that SENT the bot had no `meeting` on it — the ref was read off a `meet-<row>`
 *  session id, which only a meeting opened from the rail ever has — so the meeting it created was
 *  unclaimed and came back as a second row beside the conversation that made it. The founder saw
 *  both: *"there is no need to create a new chat for that — we already have meeting owner, just
 *  attach the status to it."* Nothing here changed; the chat now carries the ref, so this loop
 *  claims it. A meeting nobody chatted about is still listed, and still becomes a chat when opened. */
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
      // The last guard, at the surface the founder was looking at: a stored label from before
      // the rule (Vexa-ai/vexa#1602) is treated as the placeholder it is, so the row falls back to
      // its meeting's title or to "Chat" rather than putting a mark on the rail.
      label: (isMachineryLabel(c.label) ? "" : c.label) || (m ? meetingTitle(m) : "Chat"),
      when,
      whenLabel: whenShort(m ? meetingWhen(m) : when, { live, now }),
      live, upcoming,
      status: statusOf(phase),
      touched: !!c.touched,
      workspaces: c.workspaces,
      target: c.target,
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
      status: statusOf(phase),
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

// ── the server's sessions — WHERE THE RAIL ACTUALLY COMES FROM ───────────────────────
//
//  THE RAIL WAS BROWSER-LOCAL AND THAT WAS THE WHOLE DEFECT (Vexa-ai/vexa#1591, founder walk
//  2026-09-06). He worked a morning on this instance — the global scaffold, a meeting chat, several
//  Extend jobs — signed in again in a new window and got an empty rail and a "first visit":
//  *"i logged in again and now see no chats and it's starting over again while it has the context"*.
//  `CHATS_KEY` is one browser's storage; the server held every one of those conversations.
//
//  So the rail is DERIVED from `GET /api/sessions` and merged with the stored list. The direction
//  is fixed and it is the whole design: **local caches, the server owns**. A session the server
//  reports exists whether or not this browser has heard of it; a stored row the server does not
//  report is kept, because a chat can be composed here before its first turn reaches the server.

/** One row of `GET /api/sessions`, tolerantly typed — this is a wire shape, and a field a server
 *  one release behind does not send must cost a fallback, never a broken rail. */
export type ServerSession = {
  session: string;
  title?: string | null;
  /** THE SERVER'S NAME FOR THIS ROW (Vexa-ai/vexa#1602) — one rule, computed once, for every
   *  client. Empty means no name is recoverable; absent means a server that predates the rule.
   *  `title` stays what it always was and is still read as the fallback. */
  label?: string | null;
  created?: number | string | null;
  last_active?: number | string | null;
  workspaces?: string[] | null;
  /** WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611) — one of `workspaces`, or null for the person's
   *  own desk. Null is also what a server one release behind sends, and the two mean the same
   *  thing today: the desk is the default either way. */
  target?: string | null;
  scaffold?: { kind?: string | null; id?: string | null } | null;
  touched?: boolean | null;
  /** THE MEETING THIS CHAT MADE (Vexa-ai/vexa#1597) — the row id, or null. Written server-side when
   *  a bot goes out from this session; null for every chat that never sent one, including a
   *  `meet-<row>` session whose id already says it. */
  meeting?: string | null;
};

/** The meeting a session id names, or null. The inverse of `meetingChatId`.
 *
 *  This is the FIRST of two answers and it is the cheaper one: `meet-<row>` is this client's own
 *  naming of a meeting's agent session, so a meeting somebody opened from the rail needs nothing on
 *  the wire. It says nothing whatever about the chat that CREATED a meeting — that chat has an
 *  ordinary `pchat-…` id — which is why the server now sends `meeting` as well and
 *  `chatsFromSessions` asks this first and the field second. */
export function meetingIdFromChatId(id: string): string | null {
  const m = /^meet-(.+)$/.exec(id ?? "");
  return m && m[1] ? m[1] : null;
}

/** Epoch seconds (what the session index stores) or milliseconds (what a JS caller would send) →
 *  milliseconds. Anything unreadable is 0, which sorts last rather than to 1970-in-the-future. */
function whenMs(v: unknown): number {
  const n = typeof v === "number" ? v : typeof v === "string" ? Date.parse(v) || Number(v) : NaN;
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n < 1e12 ? Math.round(n * 1000) : Math.round(n);
}

/** THE TWO IDS A SERVER SESSION MAY NOT BECOME (F34, and it is deliberate).
 *
 *  `main` is `units.DEFAULT_CHAT_SESSION` — anything that chats without naming a thread lands
 *  there — and it is also the id the rail used to PLANT as "Personal"; `org-setup` was the other
 *  planted row. The founder deleted both by ruling: *"where is it coming from? i did not create
 *  this chat"*. `pruneStale` removes them on every load, so admitting them from the server would
 *  make them flicker — back on every fetch, gone on every reload — which is worse than either
 *  answer. They stay out. */
const NOT_A_RAIL_ROW = new Set<string>([PERSONAL_CHAT_ID, ORG_CHAT_ID]);

/** The server's sessions as chat records. Reading state (`artifacts`, `focus`, `view`) is empty
 *  because the server holds none of it — that is precisely what the stored record is FOR, and
 *  `mergeChats` below is where the two halves meet. */
export function chatsFromSessions(rows: ServerSession[], now = Date.now()): Chat[] {
  const out: Chat[] = [];
  for (const r of rows ?? []) {
    const id = typeof r?.session === "string" ? r.session.trim() : "";
    if (!id || NOT_A_RAIL_ROW.has(id)) continue;
    // the id first (a meeting opened from the rail), then the binding (a chat that MADE one)
    const bornAsMeeting = meetingIdFromChatId(id);
    const bound = typeof r.meeting === "string" && r.meeting.trim() ? r.meeting.trim() : undefined;
    const meeting = bornAsMeeting ?? bound;
    // WHAT THE ROW IS CALLED (Vexa-ai/vexa#1602). The server computes it — the meeting's title,
    // the scaffold's label, the act's label, or the person's first words with the machinery
    // stripped — so the rail, a second window and any other client agree by construction rather
    // than by three implementations of one rule.
    const given = typeof r.label === "string" ? r.label.trim() : "";
    // The index defaults an untitled session's title to the session id — a placeholder, not a name.
    // And a server that predates `label` sends the raw stored title, which is where the founder's
    // `Active context: the u…` rows came from: it is read, but never when it is machinery.
    const title = typeof r.title === "string" ? r.title.trim() : "";
    const named = title && title !== id && !isMachineryLabel(title) ? title : "";
    const mounts = Array.isArray(r.workspaces)
      ? r.workspaces.filter((w): w is string => typeof w === "string" && !!w.trim())
      : [];
    const sc = r.scaffold && typeof r.scaffold === "object"
      ? { kind: String(r.scaffold.kind ?? ""), id: String(r.scaffold.id ?? "") } : null;
    const created = whenMs(r.created) || now;
    out.push({
      id,
      // A chat BORN as a meeting's carries no label of its own — `railRows` names it from the
      // meeting, so the row follows a rename instead of freezing whatever the first turn was called.
      //
      // A chat that CREATED a meeting keeps its own name (Vexa-ai/vexa#1597). It was a conversation
      // before it was a meeting and the person's own first sentence named it; the founder asked for
      // the meeting's STATUS on that row — *"just attach the status to it"* — not for the row to
      // become something else. `Row.status` is where the meeting shows.
      label: bornAsMeeting ? "" : (given || named || "Chat"),
      meeting,
      workspaces: mounts.length ? mounts : ["personal", "_global"],
      target: typeof r.target === "string" && r.target.trim() ? r.target.trim() : undefined,
      artifacts: [],
      scaffold: sc && sc.kind && sc.id ? sc : undefined,
      // absent → a conversation that happened, so it shows. See `_Sessions` for why that direction.
      touched: r.touched !== false,
      createdAt: created,
      lastActivityAt: whenMs(r.last_active) || created,
    });
  }
  return dedupe(out);
}

/** THE MERGE. Stored rows first, the server's folded in, and neither erases the other.
 *
 *  Field by field, and each rule is one of the two halves owning what it actually knows:
 *   · **`lastActivityAt` is the LATER of the two.** The server saw the turn sent from the other
 *     browser; this browser saw the one sent a second ago and not yet indexed.
 *   · **reading state is local, always.** `artifacts`, `focus`, `view` exist nowhere else.
 *   · **`workspaces` is local when there is one.** A stored row's mount set is the reader's — a
 *     proposal chip can rebind a chat — and the server's is the fallback for a chat this browser
 *     has never opened.
 *   · **`target` follows `workspaces`**, and for the same reason (Vexa-ai/vexa#1611): a chip this
 *     reader clicked a moment ago is not in the index it will be read from next time, and the
 *     server's is what this browser has never seen. Neither erases the other — the local write and
 *     the server write are the same act, one of them having reached the other side already.
 *   · **`touched` is either.** Both are evidence a person wrote; neither un-writes it.
 *   · **`meeting` is local-first.** A binding this browser made moments ago is not yet in the index
 *     it will be read from next time; the server's is what a second window has never seen. Neither
 *     erases the other and there is nothing to reconcile — the binding is a latch on both sides.
 *   · **a name beats a placeholder**, whichever side holds it. A chat NAMED BY ITS MEETING has an
 *     empty label on both sides, so it keeps it — this used to be spelled as a separate "either side
 *     names a meeting ⇒ keep the local label" branch, whose premise stopped being true the moment a
 *     named conversation could bind a meeting (Vexa-ai/vexa#1597). It said what the placeholder rule
 *     already says for the case it was written for, and the wrong thing for the new one: a chat
 *     still called "New chat" that sent a bot would have been barred from taking the real title the
 *     server holds for it.
 *
 *  `hidden` is the rail's own delete. Removing a row here never removed the agent session (the
 *  comment on `deleteChat` says so), and with the rail derived from those sessions a delete would
 *  otherwise come back on the next sign-in. */
export function mergeChats(local: Chat[], server: Chat[], hidden: string[] = []): Chat[] {
  const drop = new Set(hidden ?? []);
  const by = new Map<string, Chat>();
  for (const c of local) by.set(c.id, c);
  for (const s of server) {
    const l = by.get(s.id);
    if (!l) { by.set(s.id, s); continue; }
    by.set(s.id, {
      ...l,
      label: isPlaceholderLabel(l.label) && !isPlaceholderLabel(s.label) ? s.label : l.label,
      meeting: l.meeting ?? s.meeting,
      workspaces: l.workspaces?.length ? l.workspaces : s.workspaces,
      target: l.target ?? s.target,
      scaffold: l.scaffold ?? s.scaffold,
      touched: !!l.touched || !!s.touched,
      createdAt: Math.min(l.createdAt || s.createdAt, s.createdAt || l.createdAt),
      lastActivityAt: Math.max(l.lastActivityAt || 0, s.lastActivityAt || 0),
    });
  }
  return [...by.values()].filter((c) => !drop.has(c.id));
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

/** A CHAT THAT MADE A MEETING BECOMES THAT MEETING'S CHAT (Vexa-ai/vexa#1597).
 *
 *  Founder, 2026-09-06, in a meeting he had started from a chat: *"if chat is a specific meeting —
 *  and that's a chat feature that it gets after creating meeting from itself — this transcript
 *  should be pinned. and the chat itself should be Live (left sidebar), while there is no need to
 *  create a new chat for that — we already have meeting owner, just attach the status to it"*.
 *
 *  Binding the ref is the whole of it, and everything else follows from the ref existing: `railRows`
 *  claims the meeting so it stops appearing as a second row, puts `live` (later `held`) on this one,
 *  the header wears the meeting badge, and the room's own pages open beside the conversation.
 *
 *  A LATCH, and that is the rule worth stating. A chat already about a meeting is NOT rebound by a
 *  second send: its identity is what the reader is looking at, and quietly moving the room, the
 *  pinned transcript and the note under them would be worse than the duplicate row this fixes. A
 *  second meeting is a second chat. (The server's index latches identically — one rule, both sides.)
 *
 *  Returns the SAME array when nothing changed, so a caller can persist on identity. */
export function bindMeeting(chats: Chat[], chatId: string, meetingId: string): Chat[] {
  const id = String(meetingId ?? "").trim();
  if (!id) return chats;
  const i = chats.findIndex((c) => c.id === chatId);
  if (i < 0 || chats[i].meeting) return chats;
  const next = [...chats];
  next[i] = { ...next[i], meeting: id };
  return next;
}

/** POINT A CHAT'S WRITES AT ONE OF ITS WORKSPACES (Vexa-ai/vexa#1611) — the client's ONE writer of
 *  `target`, whichever route asked: a chip click, a `focus` event, a restored record.
 *
 *  `""` is the person's own desk and clears the field rather than storing a second name for it —
 *  the desk is the default, so "no target" and "the desk" have to be one state or the merge above
 *  would have to decide which of two spellings wins.
 *
 *  IT ONLY EVER NAMES A WORKSPACE THE CHAT IS OVER. Targeting something not in `workspaces[]` would
 *  put a chip on a mount the panel does not have and the next turn will not carry — so the mount
 *  comes first (the caller adds it), and this is the second half. The server refuses the same shape
 *  for the same reason, so the two halves cannot answer differently. */
export function setTarget(chats: Chat[], chatId: string, target: string): Chat[] {
  const wid = (target ?? "").trim();
  return chats.map((c) => {
    if (c.id !== chatId) return c;
    if (wid && !c.workspaces.includes(wid)) return c;
    if ((c.target ?? "") === wid) return c;
    return { ...c, target: wid || undefined };
  });
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

/** A label that is MACHINERY — the founder's own sentence as a predicate: *never a bracket, never
 *  a mark, never "Active context"* (Vexa-ai/vexa#1602).
 *
 *  The rule that NAMES a row lives on the server (`shared/chat_label.py`) and this client does not
 *  reimplement it — `label` arrives computed. This is the smaller, local question the client alone
 *  can answer: **is a label already sitting in this browser one a person chose?** `vexa.minutes.
 *  chats` is full of names taken from the old rule, and without this the merge below would prefer
 *  a stored `[vexa-job:extend…` over the clean name the server now sends, because a machinery
 *  string is not "New chat" and the merge only knew those two placeholders.
 *
 *  Shape, not vocabulary. A rail row never opens with a bracket or with that narration, whatever a
 *  mark or a preamble is called this month; matching literals would need the marks copied here and
 *  would go stale the first time one is renamed. */
export function isMachineryLabel(label: string): boolean {
  const t = (label ?? "").trim();
  if (!t) return false;
  return t.startsWith("[") || t.startsWith("Active context") || t.startsWith("Active meeting");
}

/** A label NOBODY CHOSE. The `+` button mints "New chat" and a normalised record falls back to
 *  "Chat"; both are placeholders, and both may be replaced by the person's own first sentence.
 *  Machinery is a third — a row called `[prep] They click…` is not a name anybody picked, it is
 *  what the old rule left behind (Vexa-ai/vexa#1602) — so it is replaceable on exactly the same
 *  terms. Anything else is a name a human or a scaffold picked, and is never overwritten. */
export function isPlaceholderLabel(label: string): boolean {
  const t = (label ?? "").trim();
  return !t || /^new chat$/i.test(t) || /^chat$/i.test(t) || isMachineryLabel(t);
}

/** How long a rail row's name may be. The rail is 248px wide; past this the row truncates anyway,
 *  so cutting here means the ellipsis lands on a word boundary instead of mid-glyph. */
export const CHAT_TITLE_MAX = 48;

/** The person's first sentence, as a rail row's name: one line, trimmed, cut with an ellipsis. */
export function titleFromTurn(text: string): string {
  const one = (text ?? "").replace(/\s+/g, " ").trim();
  if (!one) return "";
  return one.length > CHAT_TITLE_MAX ? one.slice(0, CHAT_TITLE_MAX - 1).trimEnd() + "…" : one;
}

/** NAME A CHAT FROM ITS FIRST HUMAN TURN (founder ruling 2026-09-02, F38).
 *
 *  He worked a `+` chat for many turns — created a shared workspace in it, asked for research — and
 *  the rail still read "New chat". A conversation that has had a dozen turns and no name is a row
 *  nobody can find again.
 *
 *  Three refusals, and each of them is a rule rather than a guard:
 *
 *  · **A SCAFFOLDED CHAT KEEPS ITS OWN TITLE.** The record already named it, deliberately; letting
 *    a first turn overwrite that would swap a considered name for whatever the person typed first.
 *    (This is the same rule agent-api applies to the session title — `_title` comes from the
 *    scaffold's header when there is one — so the two halves agree by construction rather than by
 *    coincidence.)
 *  · **A MEETING CHAT IS NAMED BY ITS MEETING.** `railRows` reads the meeting's title so the row
 *    follows a rename; freezing a sentence onto it here would undo that.
 *  · **ONLY A PLACEHOLDER IS REPLACED.** A name a human or a preset chose stands.
 *
 *  And the caller's rule, which cannot be expressed here: the text MUST be a HUMAN turn. Never an
 *  agent turn, never a composed opening — an opening is machinery, and titling a row with the first
 *  48 characters of an instruction block is the same defect as painting it as the person's message. */
export function nameChat(c: Chat, text: string): Chat {
  if (c.scaffold || c.meeting || !isPlaceholderLabel(c.label)) return c;
  const t = titleFromTurn(text);
  return t ? { ...c, label: t } : c;
}

/** `nameChat` over the stored list — the array-in/array-out shape every other mutation here has. */
export function nameFromTurn(chats: Chat[], chatId: string, text: string): Chat[] {
  return chats.map((c) => (c.id === chatId ? nameChat(c, text) : c));
}

export function newChat(label: string, workspaces: string[], opts: { id?: string; touched?: boolean; meeting?: string; target?: string; scaffold?: { kind: string; id: string }; now?: number } = {}): Chat {
  const now = opts.now ?? Date.now();
  return {
    id: opts.id ?? `pchat-${now.toString(36)}`,
    label,
    meeting: opts.meeting,
    workspaces,
    // NO TARGET IS THE PERSONAL DESK (Vexa-ai/vexa#1611) — the default the founder's rule names,
    // and it is an ABSENCE rather than the string "personal" so there is one spelling of it.
    target: opts.target?.trim() || undefined,
    artifacts: [],
    scaffold: opts.scaffold,
    // `touched: false` is the DRAFT (F35): a chat the `+` button opened and nobody has written in.
    // The shell holds it in component state and never saves it; this only mints the record.
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
  if (isOrgProject(p)) return `Organisation · ${chat}`;
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
    // The personal project's built-in "main" row USED to be reconstructed here. It is not any
    // more (F34): it was a row nobody made, and `pruneStale` deletes exactly that id on the very
    // next line of `loadChats` — code that writes a row its own caller then removes is the stale
    // shape this commit is about.
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

/** THE 2026-09-02 PRUNE — a migration, run on every load, idempotent by construction.
 *
 *  The founder opened his rail and found four chats, three of which he had never made: **Personal**
 *  and **Organisation setup**, both PLANTED by the seeding this commit deletes, and a **New chat**
 *  he had made with `+` and never typed a word into. *"where is it coming from? i did not create
 *  this chat … this chat was created with + but never used, it just should not exist."*
 *
 *  Deleting the seeding stops NEW plants and does nothing whatever about the rows already sitting
 *  in his localStorage — and "clear your site data" is not a fix to hand a founder. So the load
 *  path prunes two shapes:
 *
 *    · **the two planted ids, by id.** They were written `touched: true` on purpose — an untouched
 *      structural row would have hidden behind the rail's own filter and taken admin with it — so a
 *      generic "drop the untouched ones" rule does not catch them and never could have.
 *    · **any chat with no human turn and no scaffold record.** `touched` is written at send time
 *      and at nothing else; a scaffold record means the chat was composed for an arrival. Neither
 *      of the two = nobody ever meant this row to exist. That is the `+` chat.
 *
 *  IDEMPOTENT: pruning an already-pruned list removes nothing, which is why this needs no marker
 *  key to keep in sync with — the same reasoning that makes `loadChats` trigger its project
 *  migration on an ABSENT key rather than on a second stored flag.
 *
 *  ⚠ One consequence, and it is deliberate: an untouched chat MATERIALISED by opening a meeting row
 *  is pruned too. Nothing the rail shows is lost — `railRows` derives a row for every meeting that
 *  has no chat, so the row comes straight back and opening it materialises the chat again. What
 *  goes is the saved tab set of a conversation nobody ever wrote in, which is precisely the "leaves
 *  nothing behind" the ruling asks for. */
export function pruneStale(chats: Chat[]): Chat[] {
  const planted = new Set<string>([PERSONAL_CHAT_ID, ORG_CHAT_ID]);
  return chats.filter((c) => !planted.has(c.id) && (!!c.touched || !!c.scaffold));
}

/** The stored `scaffold` field, or nothing. Exported-adjacent on purpose: it is the ONE place the
 *  pair is admitted, so "an admin-setup chat with no scaffold" has exactly one door and it is shut. */
function scaffoldRecord(raw: unknown): { kind: string; id: string } | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as { kind?: unknown; id?: unknown };
  return typeof r.kind === "string" && r.kind && typeof r.id === "string" && r.id
    ? { kind: r.kind, id: r.id }
    : undefined;
}

/** THE STRIP IS TABS PLUS ONE PREVIEW SLOT — OBSIDIAN'S RULE (PRD decision 28; founder ruling
 *  2026-09-06: *"no need to create tabs, unless there is a pinned tab. Use obsidian rule for that
 *  and tab icon is on tab"*).
 *
 *  THIS REVERSES THE HISTORY-BAR AMENDMENT. The strip was history — every navigation left a chip
 *  and twelve could stand at once — and the walk that produced this ruling opened four documents
 *  and got four tabs nobody had asked for. Obsidian instead: the page you navigate to takes the ONE
 *  preview slot, and the next page you open REPLACES it. A page becomes a tab only when somebody
 *  asks for it — the reader pins it, or a scaffold declared it.
 *
 *  So the cap on unpinned entries is one, and it is not a cap on history: there is no history tier
 *  left to cap. PINNED entries — a scaffold's declared `tabs:` and anything the reader pinned — sit
 *  at the left edge and are never evicted. A cap that could evict a pin would make pinning a
 *  suggestion.
 */
export const PREVIEW_CAP = 1;

/** THE STRIP'S ORDER, left to right: the desk · the chat's pins · the preview slot.
 *
 *  Three tiers, and the reason each sits where it does. The DESK is the product's own first entry
 *  and belongs to no chat, so it is furthest from the current page. PINS were asked for and must
 *  not scroll away, so they sit next. The PREVIEW — the page you opened and have not kept — is at
 *  the right edge because that is where your eye already is. It sorts on `at` like the history it
 *  replaced, so a stored strip written before the ruling still orders correctly on the way in. */
export function orderHistory(list: Artifact[]): Artifact[] {
  const desk = list.filter((a) => a.desk);
  const pinned = list.filter((a) => a.pinned && !a.desk);
  const rest = list.filter((a) => !a.pinned && !a.desk).sort((x, y) => (x.at ?? 0) - (y.at ?? 0));
  return [...desk, ...pinned, ...rest];
}

/** THE CHAT'S HOME — the strip's first entry, and it follows where the chat LIVES.
 *
 *  A chat over a group is at home in that group, not on the reader's own desk: opening the group's
 *  dailies and being shown your personal README first would be the panel disagreeing with the
 *  conversation. So a chat that mounts a group opens on the GROUP's README; a chat that mounts none
 *  opens on the desk.
 *
 *  WHAT COUNTS AS A GROUP is read off the mount set, and the exclusions are the two conventions the
 *  rest of this file already uses plus one the server's records introduced: `_global` is the
 *  company tier and is mounted everywhere; `personal` is the client's name for the reader's own
 *  desk; and `u_*` is the SERVER's name for a person's desk (the scaffold's `workspaces` carries
 *  `["_global", "u_priya", "grp-showb"]`). Without that third exclusion a person's own desk,
 *  arriving under its server name, would be mistaken for a group and become the home of every chat
 *  a scaffold opened. */
export function homeEntry(workspaces: string[] = []): Artifact {
  const group = workspaces.find((w) => w && w !== "_global" && w !== "personal" && !/^u_/.test(w));
  return group
    ? { path: "README.md", slug: group, label: group, desk: true }
    : { path: "README.md", label: "Desk", desk: true };
}

/** Compose a strip with the chat's home at its head. Idempotent, and it never duplicates: a chat
 *  whose scaffold happened to declare that README keeps ONE entry, promoted to the home tier. */
export function withHome(list: Artifact[], workspaces: string[] = []): Artifact[] {
  const h = homeEntry(workspaces);
  const key = artifactKey(h);
  const has = list.find((a) => artifactKey(a) === key);
  const rest = list.filter((a) => artifactKey(a) !== key && !a.desk);
  return orderHistory([{ ...(has ?? h), ...h, at: has?.at }, ...rest]);
}

/** Record that `art` is now in front. Dedups by identity, moves it to the right end, and evicts
 *  whatever was in the preview slot — never a pin. Pure: array in, array out. */
export function touchHistory(list: Artifact[], art: Artifact, now: number, cap = PREVIEW_CAP): Artifact[] {
  const key = artifactKey(art);
  const prev = list.find((a) => artifactKey(a) === key);
  // a pin that is navigated to STAYS pinned and stays at the left edge — its `at` still moves, so
  // unpinning it later drops it into history in the right place rather than at the far left.
  // `prev?.pinned ?? art.pinned` — a page ALREADY in the strip keeps whatever pin state it has (so
  // navigating to a pin does not unpin it, and navigating to an ordinary page does not pin it),
  // and a page arriving for the FIRST TIME keeps the pin it arrived with. Taking it from `prev`
  // alone silently dropped the pin on an entry that had never been touched — which is every
  // artifact event carrying `pin: true`.
  const next: Artifact = { ...(prev ?? art), ...art, pinned: prev?.pinned ?? art.pinned, desk: prev?.desk ?? art.desk, permanent: prev?.permanent ?? art.permanent, at: now };
  const kept = list.filter((a) => artifactKey(a) !== key);
  const out = orderHistory([...kept, next]);
  // EVICTION IS A CLOSE PATH (Vexa-ai/vexa#1600), so a permanent page is outside the cap the way the
  // desk is. It arrives pinned and so would survive anyway — but "the transcript cannot leave a
  // meeting chat" must not rest on a second field happening to be set.
  const unpinned = out.filter((a) => !a.pinned && !a.desk && !a.permanent);
  if (unpinned.length <= cap) return out;
  const evict = new Set(unpinned.slice(0, unpinned.length - cap).map(artifactKey));
  return out.filter((a) => a.pinned || a.desk || a.permanent || !evict.has(artifactKey(a)));
}

/** `×` on a tab: drop that entry. */
export function forgetHistory(list: Artifact[], key: string): Artifact[] {
  // the desk is a product default, not something the reader put there, so it is not theirs to
  // forget — and a strip that could lose its first entry would have no default view to fall back to
  //
  // …AND THE MEETING'S OWN PAGES, for the same reason one level up (Vexa-ai/vexa#1600, founder:
  // *"just keep a tab that can't be closed instead"*). The panel renders no `×` on them, and this
  // refuses them anyway: the button is one close path, and the rule is about all of them.
  return list.filter((a) => artifactKey(a) !== key || a.desk || a.permanent);
}

/** THE PIN, AS THE TAB CARRIES IT (founder ruling 2026-09-06: *"tab icon is on tab"*).
 *
 *  Pinning is the whole of "this one stays": a preview becomes a tab. UNPINNING has two answers and
 *  they differ by where the page is. The tab IN FRONT goes back to being the preview — it is still
 *  what you are reading, and a page you are looking at cannot vanish from the strip. A pinned tab
 *  BEHIND the one in front is simply dropped: with no history tier left there is nothing else for
 *  it to be, and keeping it would put a second unpinned entry beside the preview, which is the very
 *  accumulation the ruling removed.
 *
 *  The desk is a product default, not a pin, so it is not togglable — see `homeEntry`.
 *
 *  NEITHER IS A MEETING'S OWN PAGE (Vexa-ai/vexa#1600), and here that is not tidiness: unpinning a
 *  tab that is not in front DROPS it, so a pin control on the transcript would be the `×` under
 *  another name. The panel renders none on those tabs; this refuses one anyway. */
export function togglePinned(list: Artifact[], key: string, front: boolean, now: number): Artifact[] {
  const hit = list.find((a) => artifactKey(a) === key);
  if (!hit || hit.desk || hit.permanent) return list;
  if (!hit.pinned) return orderHistory(list.map((a) => (artifactKey(a) === key ? { ...a, pinned: true } : a)));
  const without = list.filter((a) => artifactKey(a) !== key);
  return front ? touchHistory(without, { ...hit, pinned: undefined }, now) : orderHistory(without);
}

/** THE STRIP, READABLE OFF THE RECORD — for the desk README's "Recently opened" (decision 26.4).
 *
 *  Newest first, because that is how a "recently opened" list reads; the strip renders the same
 *  data left-to-right oldest-first. Since the Obsidian ruling this is the chat's tabs plus the one
 *  page it was last on — what a chat KEEPS, not everywhere it went; the walked-past pages are
 *  reported to the desk itself by `deskTouch`, which is where that ranking actually lives. `workspace` is "" for the reader's own desk, matching the
 *  `artifact` event's convention that an empty slug means "no slug" rather than "unknown". */
export function chatHistory(c: Chat): { workspace: string; path: string; title: string; at: number }[] {
  return orderHistory(c.artifacts)
    .filter((a) => a.kind !== "meeting")
    .map((a) => ({ workspace: a.slug ?? "", path: a.path, title: a.label, at: a.at ?? 0 }))
    .sort((x, y) => y.at - x.at);
}

/** THE STRIP, AS THE RECORD STORES IT. A copy, never a re-decision.
 *
 *  This was three lines inside MinutesShell's persist effect, and it mapped every entry to
 *  `pinned: true` while dropping `at` and `desk`. Together those three fields ARE the model:
 *  nothing could age out (the cap evicts only UNPINNED), the order was lost (`orderHistory` sorts
 *  on `at`), and the home stopped being the home — decisions 28, 28.4 and 28.5 nullified by one
 *  literal, with no test able to see it because it lived in an effect nobody drove and `Page` did
 *  not carry the fields it dropped.
 *
 *  Extracted so the mapping is a function with a name, and so a mutation of it fails something. */
export function stripForRecord(pages: Artifact[]): Artifact[] {
  return pages.map((pg) => ({
    kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label,
    pinned: pg.pinned, desk: pg.desk, permanent: pg.permanent, at: pg.at,
  }));
}

/** The stored view slot, tolerant of a record written before it existed. */
function viewOf(r: Partial<Chat>): Artifact | undefined {
  const v = r.view as Artifact | undefined;
  if (!v || typeof v !== "object" || typeof v.path !== "string" || !v.path) return undefined;
  return { ...v, kind: v.kind === "meeting" ? "meeting" : undefined, label: typeof v.label === "string" ? v.label : v.path };
}

/** THE ONE-TIME COLLAPSE (PRD decision 28, as amended, and as ruled on 2026-09-06).
 *
 *  Before 28 every navigation appended a tab and nothing ever aged out — the founder's screenshot
 *  was a strip scrolled off the edge. This puts what a record already holds in order and applies
 *  the cap; under the Obsidian rule that cap is one preview slot, so a pre-28 pile collapses to the
 *  pins plus the page the reader was on rather than to a deletion of things they chose.
 *
 *    · a record with pinned entries is already post-28 — order it and move on.
 *    · a pre-28 record has no `at` at all. Stamping every entry the same instant would be a lie
 *      dressed as data, so they keep their STORED ORDER as their history order (it is the order
 *      they were appended, which is the order they were opened) and the one that was in FRONT is
 *      stamped newest, so it lands at the right edge where the reader left it.
 *    · a SCAFFOLD's declared tabs cannot be told from clicked ones on a pre-28 record, so they are
 *      pinned: a declared set that silently aged out would be a visible loss, and a couple of extra
 *      pins on a day-old chat is not.
 *
 *  Idempotent: a second pass finds `at` everywhere and only re-orders, which is a no-op. */
export function collapseUnpinned(c: Chat): Chat {
  if (!c.artifacts.length) return c;
  if (c.artifacts.some((a) => a.at !== undefined)) return { ...c, artifacts: orderHistory(c.artifacts) };
  const frontKey = c.view ? artifactKey(c.view) : c.focus;
  const stamped = c.artifacts.map((a, i) => ({
    ...a,
    pinned: a.pinned || !!c.scaffold,
    // stored order IS open order; the front page is stamped last so it sits at the right edge
    at: artifactKey(a) === frontKey ? c.artifacts.length + 1 : i + 1,
  }));
  const view = c.view ?? c.artifacts.find((a) => artifactKey(a) === c.focus) ?? c.artifacts[c.artifacts.length - 1];
  const capped = orderHistory(stamped);
  const unpinned = capped.filter((a) => !a.pinned);
  const evict = new Set(unpinned.slice(0, Math.max(0, unpinned.length - PREVIEW_CAP)).map(artifactKey));
  return { ...c, artifacts: capped.filter((a) => a.pinned || !evict.has(artifactKey(a))), view };
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
      // A record written before the field simply has none, which IS the personal desk — the
      // default, not a migration. Nothing to repair and nothing to stamp.
      target: typeof r.target === "string" && r.target.trim() ? r.target.trim() : undefined,
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
      view: viewOf(r),
      // ALL OR NOTHING (F37). A kind with no record id is the stale shape this commit deleted, and
      // re-admitting one here would let a row stored before it resurrect the pre-scaffold admin
      // render on the next load. A half-record is dropped, never repaired — including the bare
      // `scaffoldKind` string an older build wrote, which by construction had no id beside it.
      scaffold: scaffoldRecord(r.scaffold),
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
    return pruneStale(normalise(parsed, now).map(collapseUnpinned));
  }
  let legacy: LegacyProject[] = [];
  try { legacy = JSON.parse(localStorage.getItem(PROJECTS_KEY) || "[]") as LegacyProject[]; } catch { legacy = []; }
  const migrated = pruneStale(migrateProjects(Array.isArray(legacy) ? legacy : [], now));
  saveChats(migrated);
  return migrated;
}

export function saveChats(chats: Chat[]): void {
  try { localStorage.setItem(CHATS_KEY, JSON.stringify(chats)); } catch { /* ignore */ }
}

/** THE ROWS THIS READER DELETED. A tombstone list, and it exists because the rail now derives from
 *  the server: `deleteChat` has always meant *"off my rail"* rather than *"destroy the thread"*
 *  (its own comment: "its agent session stays on the server"), and without a record of the delete
 *  the next sign-in would fetch the row straight back. Local, like the choice it remembers. */
export const RAIL_HIDDEN_KEY = "vexa.minutes.chatsHidden";

export function loadHidden(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RAIL_HIDDEN_KEY) || "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string" && !!x) : [];
  } catch { return []; }
}

/** Remember one delete. Returns the new list so a caller never has to re-read to know it landed. */
export function hideChat(id: string): string[] {
  const next = [...new Set([...loadHidden(), id])];
  try { localStorage.setItem(RAIL_HIDDEN_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  return next;
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
