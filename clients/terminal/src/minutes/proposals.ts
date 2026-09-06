/** PROPOSALS — what an empty chat offers, derived from state the client already holds.
 *
 *  A chat with nothing in it is a blank page, and a blank page asks the reader to invent the first
 *  move. These chips make it instead: up to three, read straight off the meetings list, the chat
 *  list and one workspace marker. **No model call and no fetch of its own** — `proposals()` is a
 *  pure function over data the shell has in hand, so the row is decided in the same render that
 *  draws it and can be unit-tested without a browser, a backend or a clock.
 *
 *  The rules are a PRIORITY ORDER, not a menu: what is happening right now beats what is about to,
 *  which beats what just happened, which beats the pile nobody has read. The top three win. If
 *  fewer than three fire the row is padded with ONE standing suggestion, because an empty row is
 *  the blank page again.
 *
 *  Every chip carries the whole of its own behaviour: which meeting to open (`meetingId`) and the
 *  one line to say on arrival (`kick`). The shell reads those; it never re-derives them. */
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../canvas/actions";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import type { DeskFacts } from "../surfaces/workspaceApi";
import { isPlaceholderLabel, meetingTitle, meetingWhen, railRows, visibleRows, type Chat } from "./chats";

/** What a chip DOES, which is also what the shell switches on. */
export type ProposalKind = "catch-up" | "prep" | "outcome" | "review" | "setup";

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
};

/** "Starting soon" is two hours. Long enough that a chip appears before you go looking, short
 *  enough that it is about the next thing rather than the day. */
export const PREP_WINDOW_MS = 2 * 60 * 60 * 1000;

/** The kicks. Each names the reading the agent must do FIRST — a recap written without the
 *  transcript is the failure mode these chips exist to avoid. */
export const KICK = {
  "catch-up": "Catch me up on this meeting so far — read the transcript first.",
  prep: "Help me prepare for this meeting — read what exists and brief me.",
  outcome: "Tell me what came out of this meeting — decisions, owners, open items. Read the transcript first.",
} as const;

// ── DELETED 2026-09-02 (F36): GROUP_PROPOSAL, "Create a group for daily meetings" ─────────────
//
//  The garnish — a STANDING suggestion that padded the row to three whenever fewer rules fired. It
//  was the button the founder found sitting under a chat he had never created, and it is the exact
//  shape his ruling names: **buttons are scaffolded intents, not defaults.** A chip that comes from
//  a scaffold or from live state (a meeting running now, a desk nobody has ever written in)
//  says something true about this account; one that appears because the row looked short says
//  nothing, and reads as the product asking to be used. So the pad is gone with it: fewer than three
//  chips is a correct answer, and none at all is the correct answer for an account with nothing to
//  say about.

/** A clock, in the reader's own locale. Never a date: the chip only ever names a time inside the
 *  next two hours. */
function clock(ms: number): string {
  try { return new Date(ms).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  catch { return ""; }
}

/** When a meeting sits on the timeline. `start_time` is the rail's own answer; a purely SCHEDULED
 *  meeting has not started, so it only ever carries `scheduled_at` — and that is exactly the row
 *  the prep rule is about. 0 = unknown, and unknown never fires a time-bounded rule. */
function startsAt(m: MeetingMock): number {
  const started = meetingWhen(m);
  if (started) return started;
  const planned = Date.parse(m.scheduled_at ?? "");
  return Number.isFinite(planned) ? planned : 0;
}

/**
 *  1. a meeting running RIGHT NOW              → catch me up on it
 *  2. a meeting starting inside two hours      → prep me for it
 *  3. the newest held meeting nobody wrote in  → what came out of it
 *  4. rows the rail's filter is hiding         → review them (flip the chip, create nothing)
 *  5. a desk with nothing ever written in it   → set it up
 *
 *  …and NOTHING is padded in behind them (F36). Every rule above reads live state; a chip that
 *  appeared only because the row had space left was a default, and defaults are what the founder
 *  ruled out. An empty row is the honest answer when nothing is true.
 *
 *  `desk` is the server's answer about this person's desk (`GET /api/workspace/desk`), `null` until
 *  it arrives. `needsSetup` below is the whole of rule 5 and says why it is not the `.scaffolded`
 *  probe this used to be. `email` is the signed-in address, and it only ever reaches rule 5's chip.
 */
export function proposals(
  meetings: MeetingMock[],
  chats: Chat[],
  desk: DeskFacts | null,
  now: number = Date.now(),
  email?: string | null,
): Proposal[] {
  const out: Proposal[] = [];

  // 1 — live. Newest start wins if two are running, so the chip follows the one you just joined.
  const live = meetings.filter((m) => meetingPhase(m) === "live").sort((a, b) => startsAt(b) - startsAt(a))[0];
  if (live) out.push({
    id: `catch-up:${live.id}`, kind: "catch-up", meetingId: String(live.id),
    label: `Catch me up on ${meetingTitle(live)} — live now`, kick: KICK["catch-up"],
  });

  // 2 — the SOONEST meeting inside the window. A meeting whose start has already passed but which
  // has not begun is not "starting soon" any more; it is late, and a chip cannot fix that.
  const soon = meetings
    .filter((m) => meetingPhase(m) === "prep")
    .map((m) => ({ m, at: startsAt(m) }))
    .filter(({ at }) => at > 0 && at >= now && at - now <= PREP_WINDOW_MS)
    .sort((a, b) => a.at - b.at)[0];
  if (soon) out.push({
    id: `prep:${soon.m.id}`, kind: "prep", meetingId: String(soon.m.id),
    label: `Prep me for ${meetingTitle(soon.m)} at ${clock(soon.at)}`, kick: KICK.prep,
  });

  // 3 — held, and nobody has written about it. TOUCHED is the test, not "has a chat": opening a
  // meeting materialises an untouched chat, and merely opening it is not having asked anything.
  const spokenFor = new Set(chats.filter((c) => c.touched && c.meeting).map((c) => c.meeting as string));
  const held = meetings
    .filter((m) => meetingPhase(m) === "post" && !spokenFor.has(String(m.id)))
    .sort((a, b) => startsAt(b) - startsAt(a))[0];
  if (held) out.push({
    id: `outcome:${held.id}`, kind: "outcome", meetingId: String(held.id),
    label: `What came out of ${meetingTitle(held)}?`, kick: KICK.outcome,
  });

  // 4 — the same number the rail's own "All" chip shows, computed the same way, because two
  // different counts for one pile is how a surface starts lying. No chat is created and nothing is
  // asked: the chip only flips the filter.
  const rows = railRows(chats, meetings, now);
  const hidden = rows.length - visibleRows(rows, false).length;
  if (hidden > 0) out.push({
    id: "review", kind: "review", count: hidden,
    label: `Review ${hidden} new item${hidden === 1 ? "" : "s"}`,
  });

  // 5 — nothing has ever been written in this person's desk. The chip is their own first sentence.
  if (needsSetup(desk)) out.push(setupProposal(email));

  return out.slice(0, 3);
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
