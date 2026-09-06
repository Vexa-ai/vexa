/** PROPOSALS — what an empty chat offers: the person's SHORT LIST.
 *
 *  Founder, 2026-09-06 (Vexa-ai/vexa#1614), on the new-chat empty state:
 *
 *      *"let's see what we want to have here: create ad hoc google meet meeting; whatever, that is
 *      a short list that is updated by other agents when they see something as JTBD, can have up to
 *      10 items"*
 *
 *  A chat with nothing in it is a blank page, and a blank page asks the reader to invent the first
 *  move. The row makes it instead, from three different KINDS of truth:
 *
 *  1. **Derived** — what is true of this account right now, read straight off the meetings list, the
 *     chat list and the server's answer about this desk. Pure, no fetch, no clock of its own.
 *  2. **Written by other agents** — the desk's short list (`.vexa/proposals.json`, via
 *     `surfaces/proposalsApi`): a job an agent SAW while doing something else and filed with its
 *     source. Newest first, and rendered from state — the fetch is one plain file read, never a turn.
 *  3. **Standing** — always there, true of everybody: create a Meet, paste a meeting link. They are
 *     appended LAST and are never crowded out by the cap, because "always there" is what standing
 *     means.
 *
 *  The derived rules are a PRIORITY ORDER, not a menu: what is happening right now beats what is
 *  about to, which beats what just happened, which beats the pile nobody has read. Ten, not three
 *  (#1614), and the cap is applied to 1 + 2 so that 3 always fits.
 *
 *  Every chip carries the whole of its own behaviour: which meeting to open (`meetingId`) and the
 *  one line to say on arrival (`kick`). The shell reads those; it never re-derives them. */
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../canvas/actions";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import type { DeskProposal } from "../surfaces/proposalsApi";
import type { DeskFacts } from "../surfaces/workspaceApi";
import { isPlaceholderLabel, meetingTitle, meetingWhen, railRows, visibleRows, type Chat } from "./chats";

/** What a chip DOES, which is also what the shell switches on. */
export type ProposalKind = "catch-up" | "prep" | "outcome" | "review" | "setup" | "jtbd" | "meet" | "link";

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
};

/** The whole row, at most this many. The founder's number: *"can have up to 10 items"* (#1614). */
export const PROPOSALS_MAX = 10;

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

/** THE STANDING ACTS — always there, true of everybody (#1614: *"standing — always there"*).
 *
 *  `meet` HAS TWO BRANCHES AND ONLY ONE OF THEM EXISTS TODAY. The founder's shape: *"when a Google
 *  account is connected it creates the Meet and sends the bot in one act; when not, the act is
 *  'connect Google' first, said plainly"*. Nothing in this product can create a Meet yet — there is
 *  no Google API client in the repository and the OAuth client is a sign-in provider with no
 *  calendar scope (`app/api/googleMeet.ts` names the three missing pieces) — so `googleMeet` is
 *  false everywhere and the connect branch is what ships. SAID PLAINLY is the whole requirement: the
 *  chip does not offer to make a Meet and then explain that it cannot.
 *
 *  `link` is the act that already works: `request_meeting_bot` puts the bot in any meeting whose
 *  link you hand it, so the chip opens that conversation rather than pretending to be a form. */
export function standingProposals(googleMeet: boolean): Proposal[] {
  const meet: Proposal = googleMeet
    ? {
        id: "meet", kind: "meet", label: "Create a Google Meet and put Vexa in it",
        say: "Create an ad hoc Google Meet and put Vexa in it.",
        kick: "Create an ad hoc Google Meet for me now and send the Vexa bot into it. Give me the "
          + "link when it is in.",
        title: "New meeting",
      }
    : {
        id: "meet", kind: "meet", label: "Connect Google, so I can create meetings for you",
        say: "Connect my Google account so you can create meetings for me.",
        kick: "I want you to be able to create an ad hoc Google Meet and put Vexa in it. That needs "
          + "a Google account connected first. Tell me what is missing and what I have to do.",
        title: "Connect Google",
      };
  return [
    meet,
    {
      id: "link", kind: "link", label: "Paste a meeting link",
      say: "Put Vexa in a meeting — I'll paste the link.",
      kick: "I want Vexa in a meeting. Ask me for the link, then send the bot in and tell me when "
        + "it has been admitted.",
      title: "Put Vexa in a meeting",
    },
  ];
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

/**
 *  1. a meeting running RIGHT NOW              → catch me up on it
 *  2. a meeting starting inside two hours      → prep me for it
 *  3. the newest held meeting nobody wrote in  → what came out of it
 *  4. rows the rail's filter is hiding         → review them (flip the chip, create nothing)
 *  5. a desk with nothing ever written in it   → set it up
 *  6. the short list other agents wrote        → the job, newest first, with its source
 *  …then the standing acts, which the cap never crowds out.
 *
 *  …and NOTHING is padded in behind them (F36). Every rule above reads live state; a chip that
 *  appeared only because the row had space left was a default, and defaults are what the founder
 *  ruled out. What stands at the end stands for a different reason: #1614 asks for those two on
 *  every empty chat, by name.
 *
 *  `desk` is the server's answer about this person's desk (`GET /api/workspace/desk`), `null` until
 *  it arrives. `needsSetup` below is the whole of rule 5 and says why it is not the `.scaffolded`
 *  probe this used to be. `email` is the signed-in address, and it only ever reaches rule 5's chip.
 *  `items` is the desk's short list, already ordered by the store. `googleMeet` picks the standing
 *  Meet act's branch.
 *
 *  ⚠ ON RULE 5 AND #1614. The founder's #1614 text says the setup chip *"goes (it belongs to the
 *  arrival, #1613's third part)"*. #1613's third part landed first and kept the chip here with its
 *  derivation repaired: `needsSetup` now reads the FILES, so the stale offer he actually met — over
 *  a desk that had been running for forty minutes — cannot happen again. Both intents are kept: the
 *  defect is fixed where #1613 fixed it, and this row is the short list #1614 asked for. Deleting
 *  the chip outright is a founder call, not a merge decision — it is asked on the issue.
 */
export function proposals(
  meetings: MeetingMock[],
  chats: Chat[],
  desk: DeskFacts | null,
  now: number = Date.now(),
  email?: string | null,
  items: DeskProposal[] = [],
  googleMeet: boolean = false,
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

  // 6 — what other agents put on this desk. Newest first is the store's own order; nothing here
  // re-sorts it, because `since` is the FIRST sighting and the server is the one that knows it.
  for (const item of items) out.push(jtbdProposal(item));

  // THE STANDING ACTS SURVIVE THE CAP. Everything above is what happens to be true today; these two
  // are what this product is for, and a row that dropped them because a busy week filled it would
  // be a product hiding its own front door on exactly the days somebody needs it.
  const standing = standingProposals(googleMeet);
  return [...out.slice(0, Math.max(0, PROPOSALS_MAX - standing.length)), ...standing];
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
