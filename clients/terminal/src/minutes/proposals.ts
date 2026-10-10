/** PROPOSALS — what an empty chat offers: at most FOUR things, each specific, current and one
 *  click from a real result.
 *
 *  History: #1614 (2026-09-06) asked for a short list of up to ten, with two "standing" acts on every
 *  row. The founder met that row on app.dev on 2026-10-10 and said it "does not make sense":
 *
 *    · "What came out of Google Meet · rhw-qwts-fvi?" — the meeting had no title, so the chip
 *      printed the platform and the raw code, with nothing saying WHEN it happened;
 *    · "Review 2 new items" — never said what the items were;
 *    · "Connect Google, so I can create meetings for you" — offered to someone who HAD connected
 *      Google, because the chip read a deployment flag (`/api/config` → `google.meet`, false
 *      everywhere) instead of the person's own connections;
 *    · "Paste a meeting link" — an instruction, not an act: clicking it asked the agent to ask for
 *      a link.
 *
 *  So every chip now answers three questions on its face — WHAT (a meeting by its name, never a raw
 *  code), WHEN (relative time), and WHAT HAPPENS ON CLICK (a real result) — and the row is ranked:
 *
 *    1. a meeting running now                  → catch up on it
 *    2. a meeting later today (or within 2 h)  → prep for it
 *    3. a meeting that just ended (≤ 7 days), nobody has written about it → recap it
 *    4. items waiting: the rail's hidden rows, NAMED; then the jobs other agents filed
 *    5. setup gaps: an empty desk; a Google connection that is missing or broken (broker status)
 *    6. send Vexa to a meeting — opens a paste field in place; only if there is room
 *
 *  Nothing pads the row (F36). When nothing in 1–5 fires, the row is the send act plus one short
 *  line (`emptyLine`) rather than filler. */
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../platform";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import type { DeskProposal } from "../surfaces/proposalsApi";
import type { DeskFacts } from "../surfaces/workspaceApi";
import { isPlaceholderLabel, meetingTitle, meetingWhen, railRows, visibleRows, type Chat } from "./chats";

/** What a chip DOES, which is also what the shell switches on. */
export type ProposalKind = "catch-up" | "prep" | "outcome" | "review" | "setup" | "jtbd" | "connect" | "link";

export type Proposal = {
  id: string;           // stable across renders — the React key, and what a test names
  kind: ProposalKind;
  label: string;        // the chip's whole text
  meetingId?: string;   // catch-up | prep | outcome — the meeting the chip BINDS THIS CHAT TO
  kick?: string;        // the one line fired into this chat on click (absent = no turn)
  say?: string;         // the kick's VISIBLE form — set when the chip's words are the user's own,
                        // so the turn renders as their message instead of arriving hidden
  title?: string;       // the name this chat takes if nobody has named it yet (see isUnlabeled)
  count?: number;       // review — how many rows the rail is hiding
  source?: string;      // jtbd — WHERE the job was seen, in human words. Rendered beside the act,
                        // because an item somebody else wrote has to say what it came from.
  itemId?: string;      // jtbd — the store row this chip is, so a click or a dismiss can close it
  provider?: string;    // connect — the broker provider the Connections panel opens on
  connectionId?: string;// connect — the broken connection to repair (absent = a new one)
};

/** The whole row, at most this many (founder, 2026-10-10: "up to four, ranked by usefulness"). */
export const PROPOSALS_MAX = 4;

/** "Starting soon" when it is not today any more (just after midnight) — two hours. */
export const PREP_WINDOW_MS = 2 * 60 * 60 * 1000;

/** "Just ended": a held meeting older than this is history, not something to recap from a chip. */
export const RECAP_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

/** The one line an empty chat shows when nothing above fires. Friendly, short, and true. */
export const EMPTY_LINE = "Nothing is waiting for you. Ask me anything, or send Vexa to a meeting.";

/** The kicks. Each names the reading the agent must do FIRST — a recap written without the
 *  transcript is the failure mode these chips exist to avoid. */
export const KICK = {
  "catch-up": "Catch me up on this meeting so far — read the transcript first.",
  prep: "Help me prepare for this meeting — read what exists and brief me.",
  outcome: "Tell me what came out of this meeting — decisions, owners, open items. Read the transcript first.",
} as const;

// ── naming a meeting ─────────────────────────────────────────────────────────────────

/** Is this title one somebody GAVE the meeting, or the list's own platform·code fallback
 *  (`surfaces/liveMeetings.ts`: "Google Meet · abc-defg-hij", "Untitled meeting")? */
function hasRealTitle(m: MeetingMock): boolean {
  if ((m.title_custom ?? "").trim()) return true;
  const t = String(m.title ?? "").trim();
  if (!t || t === "Untitled meeting") return false;
  return !(m.native_id && t.includes(m.native_id));
}

/** A meeting's name for a chip — its title, or "<Platform> call" when it has none. NEVER the raw
 *  meeting code: a code identifies a room, not a conversation anybody remembers. */
export function meetingName(m: MeetingMock): string {
  if (hasRealTitle(m)) return meetingTitle({ ...m, title: (m.title_custom ?? "").trim() || m.title });
  const p = String(m.platform ?? "").trim();
  const platform = p === "google_meet" ? "Google Meet" : p === "zoom" ? "Zoom" : p === "teams" ? "Teams"
    : p === "jitsi" ? "Jitsi" : p;
  return platform ? `${platform} call` : "Untitled meeting";
}

function clock(ms: number): string {
  try { return new Date(ms).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  catch { return ""; }
}

const dayStart = (ms: number) => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };

/** WHEN, relative to now (design guidelines §5.3): "today 3:00 PM", "yesterday 3:00 PM",
 *  "tomorrow 9:30 AM", "Thu 3:00 PM" within a week, "Oct 8" beyond — the year only when it is not
 *  this one. 0 (unknown) → "". */
export function relativeWhen(ms: number, now: number = Date.now()): string {
  if (!ms) return "";
  try {
    const days = Math.round((dayStart(ms) - dayStart(now)) / 86400000);
    if (days === 0) return `today ${clock(ms)}`;
    if (days === -1) return `yesterday ${clock(ms)}`;
    if (days === 1) return `tomorrow ${clock(ms)}`;
    const d = new Date(ms);
    if (Math.abs(days) < 7) return `${d.toLocaleDateString(undefined, { weekday: "short" })} ${clock(ms)}`;
    return d.getFullYear() === new Date(now).getFullYear()
      ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  } catch { return ""; }
}

/** When a meeting sits on the timeline. `start_time` is the rail's own answer; a purely SCHEDULED
 *  meeting has not started, so it only ever carries `scheduled_at`. 0 = unknown, and unknown never
 *  fires a time-bounded rule. */
function startsAt(m: MeetingMock): number {
  const started = meetingWhen(m);
  if (started) return started;
  const planned = Date.parse(m.scheduled_at ?? "");
  return Number.isFinite(planned) ? planned : 0;
}

/** "<name>, <when>" — the when dropped when unknown, never an empty comma. */
const named = (m: MeetingMock, now: number) => {
  const w = relativeWhen(startsAt(m), now);
  return w ? `${meetingName(m)}, ${w}` : meetingName(m);
};

// ── setup gaps: the person's own connections, as the broker reports them ─────────────

/** One row of `GET /api/connections/list` (the broker's `/api/connections`, metadata only). */
export type ConnectionRow = { id: string; provider: string; label?: string; status: string };

const PROVIDER_NAME: Record<string, string> = { google_calendar: "Google Calendar", google_email: "Gmail" };

/** A connect chip — ONLY when a connection is missing or broken, read off the broker's statuses:
 *
 *    · `null` (the list has not answered, or could not be read) → nothing. A chip offering to connect
 *      what may well be connected is the exact lie the founder met; unknown fails closed.
 *    · a Google connection the broker reports `disconnected` → "Reconnect <it>"
 *    · one stuck at `awaiting_user` with no ready twin → "Finish connecting <it>"
 *    · no Google Calendar at all → "Connect Google Calendar" — the one connection the meeting jobs
 *      above read (upcoming meetings). Gmail is not offered unprompted.
 *    · otherwise (everything `ready`) → nothing.
 *
 *  The click opens the Connections panel on that provider; consent happens there, on a human click. */
export function connectionGap(rows: ConnectionRow[] | null): Proposal | null {
  if (!rows) return null;
  const google = rows.filter((r) => r.provider in PROVIDER_NAME);
  const ready = new Set(google.filter((r) => r.status === "ready").map((r) => r.provider));
  const chip = (provider: string, label: string, connectionId?: string): Proposal =>
    ({ id: `connect:${provider}`, kind: "connect", label, provider, connectionId });
  for (const provider of ["google_calendar", "google_email"]) {
    if (ready.has(provider)) continue;
    const broken = google.find((r) => r.provider === provider && r.status === "disconnected");
    if (broken) return chip(provider, `Reconnect ${PROVIDER_NAME[provider]}`, broken.id);
    const pending = google.find((r) => r.provider === provider && r.status === "awaiting_user");
    if (pending) return chip(provider, `Finish connecting ${PROVIDER_NAME[provider]}`, pending.id);
  }
  if (!ready.has("google_calendar")) return chip("google_calendar", "Connect Google Calendar");
  return null;
}

// ── send Vexa to a meeting ───────────────────────────────────────────────────────────

/** The chip that OPENS a paste field in place (`ProposalChips`). It carries no kick: nothing is said
 *  until there is a link to say it about. */
export const SEND_BOT: Proposal = { id: "link", kind: "link", label: "Send Vexa to a meeting" };

/** The pasted link, as the turn the chip fires. The person's own words render ("Send Vexa to …");
 *  the kick names the tool, so the agent acts rather than asks again. Only ever built from a link
 *  `parseMeetingInput` accepted. */
export function linkProposal(url: string): Proposal {
  const u = url.trim();
  return {
    id: "link", kind: "link", label: SEND_BOT.label,
    say: `Send Vexa to ${u}`,
    kick: `Send the Vexa bot to this meeting now: ${u}\nUse request_meeting_bot with that link, then `
      + "tell me when it has been admitted.",
    title: "Vexa in a meeting",
  };
}

/** One row of the desk's short list, as a chip.
 *
 *  THE ACT IS THE WRITER'S OWN WORDS, and it is what the person SAYS — `say` renders the turn as
 *  their message rather than as machinery arriving from nowhere, the same rule the setup chip is
 *  built on. The `kick` adds the one thing the act cannot carry and the agent needs: where it came
 *  from, and the instruction to read before writing. `source` is rendered beside the act, because an
 *  item somebody else put on your list has to say what it came from. */
export function jtbdProposal(item: DeskProposal): Proposal {
  const from = (item.source_label || "").trim();
  return {
    id: `jtbd:${item.id}`,
    itemId: item.id,
    kind: "jtbd",
    label: item.act,
    source: from,
    say: item.act,
    kick: `${item.act}\n\n(This came out of ${from || item.source}. Read what exists first, then `
      + `help me do it.)`,
    title: item.act.slice(0, 60),
  };
}


/** The row, decided. Pure: meetings, chats, the desk's facts, its short list and the person's
 *  connections in; at most four chips out, ranked (see the file head).
 *
 *  `desk` is `GET /api/workspace/desk` (null until it answers); `email` only ever reaches the setup
 *  chip; `items` is the desk's short list, already ordered by the store; `connections` is the
 *  broker's list (null until it answers — and null offers no connect chip). */
export function proposals(
  meetings: MeetingMock[],
  chats: Chat[],
  desk: DeskFacts | null,
  now: number = Date.now(),
  email?: string | null,
  items: DeskProposal[] = [],
  connections: ConnectionRow[] | null = null,
): Proposal[] {
  const out: Proposal[] = [];

  // 1 — live. Newest start wins if two are running, so the chip follows the one you just joined.
  const live = meetings.filter((m) => meetingPhase(m) === "live").sort((a, b) => startsAt(b) - startsAt(a))[0];
  if (live) out.push({
    id: `catch-up:${live.id}`, kind: "catch-up", meetingId: String(live.id),
    label: `Catch up: ${meetingName(live)}, live now`, kick: KICK["catch-up"],
  });

  // 2 — the SOONEST meeting still ahead today, or inside two hours (which covers just after
  // midnight). A start that has passed without the meeting beginning is late, not upcoming.
  const soon = meetings
    .filter((m) => meetingPhase(m) === "prep")
    .map((m) => ({ m, at: startsAt(m) }))
    .filter(({ at }) => at > 0 && at >= now && (dayStart(at) === dayStart(now) || at - now <= PREP_WINDOW_MS))
    .sort((a, b) => a.at - b.at)[0];
  if (soon) out.push({
    id: `prep:${soon.m.id}`, kind: "prep", meetingId: String(soon.m.id),
    label: `Prep: ${named(soon.m, now)}`, kick: KICK.prep,
  });

  // 3 — just ended, and nobody has written about it. TOUCHED is the test, not "has a chat": opening
  // a meeting materialises an untouched chat, and merely opening it is not having asked anything.
  const spokenFor = new Set(chats.filter((c) => c.touched && c.meeting).map((c) => c.meeting as string));
  const held = meetings
    .filter((m) => meetingPhase(m) === "post" && !spokenFor.has(String(m.id)))
    .filter((m) => { const t = startsAt(m); return t > 0 && now - t <= RECAP_WINDOW_MS; })
    .sort((a, b) => startsAt(b) - startsAt(a))[0];
  if (held) out.push({
    id: `outcome:${held.id}`, kind: "outcome", meetingId: String(held.id),
    label: `Recap: ${named(held, now)}`, kick: KICK.outcome,
  });

  // 4 — waiting. The rail's hidden pile, with the SAME count its "All" chip shows (two counts for one
  // pile is how a surface starts lying) — but now saying what is in it. The click only flips the
  // filter. Then the jobs other agents filed, in the store's order.
  const review = reviewProposal(chats, meetings, now);
  if (review) out.push(review);
  for (const item of items) out.push(jtbdProposal(item));

  // 5 — setup gaps: a desk nothing was ever written in; a Google connection missing or broken.
  if (needsSetup(desk)) out.push(setupProposal(email));
  const gap = connectionGap(connections);
  if (gap) out.push(gap);

  // 6 — the send act, when there is room. It is a real act (it opens a paste field), so it is
  // never filler; but it does not push a meeting that needs you off the row.
  const top = out.slice(0, PROPOSALS_MAX);
  return top.length < PROPOSALS_MAX ? [...top, SEND_BOT] : top;
}

/** Rule 4's chip: "2 meetings to review: Campbell sync, Google Meet call". Names the first two
 *  rows and counts the rest, so the reader knows what they are being asked to look at. */
export function reviewProposal(chats: Chat[], meetings: MeetingMock[], now: number = Date.now()): Proposal | null {
  const rows = railRows(chats, meetings, now);
  const visible = new Set(visibleRows(rows, false).map((r) => r.key));
  const hidden = rows.filter((r) => !visible.has(r.key));
  if (!hidden.length) return null;
  const byId = new Map(meetings.map((m) => [String(m.id), m] as const));
  const nameOf = (r: (typeof rows)[number]) => {
    const m = r.meetingId ? byId.get(r.meetingId) : undefined;
    return m ? meetingName(m) : (isPlaceholderLabel(r.label) ? "Untitled chat" : r.label);
  };
  const n = hidden.length;
  const allMeetings = hidden.every((r) => r.meetingId && byId.has(r.meetingId));
  const noMeetings = hidden.every((r) => !r.meetingId);
  const noun = allMeetings ? (n === 1 ? "meeting" : "meetings")
    : noMeetings ? (n === 1 ? "chat" : "chats") : "meetings and chats";
  const names = hidden.slice(0, 2).map(nameOf).join(", ");
  const more = n > 2 ? ` +${n - 2}` : "";
  return { id: "review", kind: "review", count: n, label: `${n} ${noun} to review: ${names}${more}` };
}

/** The line under an empty chat: shown only when nothing but the send act is on offer. */
export function emptyLine(chips: Proposal[]): string | null {
  return chips.every((p) => p.kind === "link") ? EMPTY_LINE : null;
}

/** MAY WE OFFER TO SET THIS PERSON UP? (Vexa-ai/vexa#1613.)
 *
 *  The founder opened a new chat at 14:10 and it offered him *"My email is dmitry@vexa.ai, set up a
 *  workspace for me"* — over a desk that had existed since 13:30 and already held company, person
 *  and project entities. The chip was reading `.scaffolded`, a marker written by exactly one route
 *  (the personal onboarding conversation, as its last act) and which `flows_defs/production.py`
 *  describes as *"a harmless marker; it gates nothing"*. Its ABSENCE was being read as "this person
 *  has never been set up", and that has not been what it means since a desk acquired other ways to
 *  come into existence.
 *
 *  So the derivation is pinned here, on the server's own answer about the FILES:
 *
 *    · no facts yet          → offer nothing (fails closed: a chip that arrives a second late is a
 *                              flicker; one offered to somebody already set up is a lie)
 *    · the marker is there   → a setup conversation finished. Nothing to offer.
 *    · the desk is `warm` or `pile` → something is written in it. Nothing to offer.
 *    · the desk is `new`     → nothing has ever been written here. Offer.
 *
 *  Exported and pure so the rule is testable on its own, which is the half that broke. */
export function needsSetup(desk: DeskFacts | null): boolean {
  if (!desk) return false;
  if (desk.scaffolded) return false;
  return desk.state === "new";
}

/** Rule 5's chip, written as the person's own opening line. Founder shape (2026-09-01): the button
 *  IS the first thing they say — "My email is <theirs>, set up a workspace for me" — so clicking it
 *  starts the setup conversation with an answer already in it rather than with a button press nobody
 *  can see afterwards. The address comes from the signed-in session and is never typed; without one
 *  the sentence would read "My email is , …", so it degrades to the plain ask.
 *
 *  The kick carries the discovery-loop grounding the composer would otherwise attach to a first
 *  onboarding reply — the chip skips the composer, so it brings the grounding itself. `say` is what
 *  the reader sees; the grounding never renders (compactStoredUserText strips it on reload too). */
export function setupProposal(email?: string | null): Proposal {
  const say = email ? `My email is ${email}, set up a workspace for me` : "Set up a workspace for me";
  return { id: "setup", kind: "setup", label: say, say, kick: ONBOARDING_GROUNDING + ONBOARDING_REPLY_SEP + say, title: "Workspace setup" };
}

// ── what a click DOES ────────────────────────────────────────────────────────────────
//
//  A chip ACTS IN THE CHAT IT RENDERS IN. Founder ruling, 2026-09-01: he pressed one inside a chat
//  he had just created and got a second one — *"clicking this button should not create a new chat —
//  this chat is already new."* So nothing below ever appends a row: the record in front is touched,
//  named and — for a meeting chip — REBOUND to the meeting, keeping its id, which is also its agent
//  session. `applyProposal` is the whole mutation and it is pure, so the contract is testable at the
//  boundary that actually broke rather than through a rendered shell.

/** A label nobody chose. One definition, in chats.ts, because the rail's naming rule (F38) and this
 *  one are the same question — "may this name be replaced?" — and two copies of it would drift. */
export const isUnlabeled = isPlaceholderLabel;

// ── DELETED 2026-09-02 (F34): the STRUCTURAL set ──────────────────────────────────────────────
//
//  It named the two SEEDED rows (`main`, `org-setup`) as the one place a meeting chip refused to
//  rebind, because turning "Personal" into a meeting's chat would have retired the home row for
//  good. Neither row is planted any more and `pruneStale` deletes both from anyone who still has
//  them, so the refusal has nothing left to refuse. The rule it protected survives where it is
//  still true: a chat already bound to a DIFFERENT meeting is not rebound either.

export type ProposalEffect =
  /** review — flip the rail's own filter. Touches no chat, names none, relabels nothing. */
  | { act: "filter" }
  /** connect — open the Connections panel on that provider (and that connection, to repair it).
   *  Touches no chat; consent is a human click inside the panel. */
  | { act: "connect"; provider: string; connectionId?: string }
  /** link with no link yet — open the paste field in place. Touches no chat, says nothing. */
  | { act: "paste" }
  /** the chip acts IN `chat` — same id as the one in front, already touched, named and rebound. */
  | { act: "run"; chat: Chat; kick?: string; say?: string }
  /** the chat in front may not be rebound (structural, or bound to a DIFFERENT meeting) — open the
   *  meeting's own chat, as the rail does. */
  | { act: "open"; meetingId: string; kick?: string; say?: string }
  /** the degenerate case: nothing is in front at all. */
  | { act: "create"; label: string; kick?: string; say?: string };

/** A chip plus the chat in front, in — the whole mutation, out. `null` = do nothing.
 *
 *  A meeting chip REBINDS: the record keeps its id (so no row appears and the turn lands in the
 *  session already open), takes the meeting's ref and title, and DROPS its saved tabs so `openChat`
 *  seeds the room from the meeting's phase pages instead of reopening yesterday's README. A chat
 *  already bound to that meeting has nothing to rebind and is simply asked.
 *
 *  Two chats on one meeting is legal — they are bundles, not the meeting — so a meeting that already
 *  has a chat with history does NOT divert the click into it. */
export function applyProposal(
  p: Proposal,
  current: Chat | null | undefined,
  meetings: MeetingMock[],
  now: number = Date.now(),
): ProposalEffect | null {
  if (p.kind === "review") return { act: "filter" };
  if (p.kind === "connect") return { act: "connect", provider: p.provider ?? "google_calendar", connectionId: p.connectionId };
  if (p.kind === "link" && !p.kick) return { act: "paste" };
  const touch = (c: Chat): Chat => ({ ...c, touched: true, lastActivityAt: now });

  if (p.meetingId) {
    const m = meetings.find((x) => String(x.id) === p.meetingId);
    if (!m) return null;                                        // a chip for a meeting the list lost
    if (current && current.meeting === p.meetingId) return { act: "run", chat: touch(current), kick: p.kick, say: p.say };
    if (!current || current.meeting)
      return { act: "open", meetingId: p.meetingId, kick: p.kick, say: p.say };
    return {
      act: "run",
      chat: { ...touch(current), meeting: p.meetingId, label: meetingTitle(m), artifacts: [], focus: undefined },
      kick: p.kick, say: p.say,
    };
  }

  if (!current) return { act: "create", label: p.title ?? "Chat", kick: p.kick, say: p.say };
  const named = p.title && isUnlabeled(current.label) ? { ...touch(current), label: p.title } : touch(current);
  return { act: "run", chat: named, kick: p.kick, say: p.say };
}
