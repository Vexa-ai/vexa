/** The empty chat's proposal chips, tested at the ONE function that decides them.
 *
 *  `proposals()` is pure — meetings in, chats in, one workspace marker in, chips out — so every
 *  rule, the priority between them and the padding are decidable here without a browser, a backend
 *  or a real clock. That is the whole reason the rule lives in a function instead of in JSX.
 *
 *  Covered: each of the five rules alone · the priority order when they collide · the three-chip
 *  cap · the ONE static pad · the two ways a rule declines to fire (unknown scaffolding, a meeting
 *  already written about). */
import { describe, expect, it } from "vitest";
import type { MeetingMock } from "../../surfaces/meetingModel";
import type { Chat } from "../chats";
import { applyProposal, GROUP_PROPOSAL, isUnlabeled, KICK, PREP_WINDOW_MS, proposals, setupProposal } from "../proposals";
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../../canvas/actions";
import { ORG_CHAT_ID, PERSONAL_CHAT_ID } from "../chats";

const NOW = Date.UTC(2026, 8, 1, 12, 0, 0);            // a fixed "now" — nothing here reads the clock
const at = (mins: number) => new Date(NOW + mins * 60000).toISOString();

const meeting = (id: string, title: string, live_status: string, startMins: number): MeetingMock =>
  ({ id, title, status: live_status === "active" ? "live" : "past", live_status,
     native_id: `n-${id}`, start_time: at(startMins) } as unknown as MeetingMock);

const LIVE = meeting("m-live", "Standup — daily", "active", -12);
const SOON = meeting("m-prep", "Acme — pricing review", "scheduled", 75);
const HELD = meeting("m-post", "Blue Light Card — discovery", "completed", -1500);

const chat = (over: Partial<Chat> & { id: string }): Chat => ({
  label: over.id, workspaces: ["personal", "_global"], artifacts: [],
  createdAt: NOW, lastActivityAt: NOW, ...over,
});
/** A chat the rail's default filter SHOWS — so it never contributes to the review count. */
const touched = (id: string) => chat({ id, touched: true });
/** An auto-created chat nobody has written in — exactly what rule 4 counts. */
const untouched = (id: string) => chat({ id, touched: false });

const run = (m: MeetingMock[], c: Chat[], scaffolded: boolean | null = true) => proposals(m, c, scaffolded, NOW);
const labels = (ps: { label: string }[]) => ps.map((p) => p.label);
const kinds = (ps: { kind: string }[]) => ps.map((p) => p.kind);

describe("rule 1 — a meeting running right now", () => {
  it("offers a catch-up naming the meeting, bound to it, with the read-first kick", () => {
    const [p] = run([LIVE], [touched("main")]);
    expect(p.kind).toBe("catch-up");
    expect(p.label).toBe("Catch me up on Standup — live now");   // the title's own qualifier is dropped
    expect(p.meetingId).toBe("m-live");
    expect(p.kick).toBe(KICK["catch-up"]);
  });

  it("two running meetings produce ONE chip — the one that started most recently", () => {
    const older = meeting("m-live-2", "Retro", "active", -40);
    const ps = run([older, LIVE], [touched("main")]);
    expect(kinds(ps).filter((k) => k === "catch-up")).toHaveLength(1);
    expect(ps[0].meetingId).toBe("m-live");
  });
});

describe("rule 2 — a meeting starting soon", () => {
  it("offers prep, naming the meeting and its clock time", () => {
    const [p] = run([SOON], [touched("main")]);
    expect(p.kind).toBe("prep");
    expect(p.label).toContain("Prep me for Acme at ");
    expect(p.meetingId).toBe("m-prep");
    expect(p.kick).toBe(KICK.prep);
  });

  it("a meeting beyond the two-hour window does not fire it", () => {
    const later = meeting("m-late", "Board", "scheduled", PREP_WINDOW_MS / 60000 + 1);
    expect(kinds(run([later], [touched("main")]))).not.toContain("prep");
  });

  it("a scheduled meeting whose start has already passed is late, not soon", () => {
    const overdue = meeting("m-overdue", "Missed", "scheduled", -5);
    expect(kinds(run([overdue], [touched("main")]))).not.toContain("prep");
  });

  it("the SOONEST of several in the window wins", () => {
    const nearer = meeting("m-near", "Design sync", "scheduled", 20);
    const ps = run([SOON, nearer], [touched("main")]);
    expect(ps.find((p) => p.kind === "prep")?.meetingId).toBe("m-near");
  });

  it("a `scheduled_at`-only meeting still resolves — a meeting that has not run has no start_time", () => {
    const planned = { id: "m-plan", title: "Kickoff", status: "past", live_status: "scheduled",
      scheduled_at: at(30) } as unknown as MeetingMock;
    expect(run([planned], [touched("main")])[0].label).toContain("Prep me for Kickoff at ");
  });
});

describe("rule 3 — the newest held meeting nobody has written about", () => {
  it("offers the outcome question, bound to the meeting", () => {
    const [p] = run([HELD], [touched("main")]);
    expect(p.kind).toBe("outcome");
    expect(p.label).toBe("What came out of Blue Light Card?");
    expect(p.meetingId).toBe("m-post");
    expect(p.kick).toBe(KICK.outcome);
  });

  it("a TOUCHED chat bound to it silences the rule — that meeting has been asked about", () => {
    const asked = chat({ id: "meet-m-post", meeting: "m-post", touched: true });
    expect(kinds(run([HELD], [touched("main"), asked]))).not.toContain("outcome");
  });

  it("merely OPENING it does not — an untouched chat is not a question asked", () => {
    const opened = chat({ id: "meet-m-post", meeting: "m-post", touched: false });
    expect(kinds(run([HELD], [touched("main"), opened]))).toContain("outcome");
  });

  it("the NEWEST unwritten held meeting wins", () => {
    const older = meeting("m-old", "Kickoff", "completed", -9000);
    expect(run([older, HELD], [touched("main")])[0].meetingId).toBe("m-post");
  });
});

describe("rule 4 — the pile the rail is hiding", () => {
  it("counts the untouched auto-created chats and creates nothing", () => {
    const [p] = run([], [touched("main"), untouched("a"), untouched("b")]);
    expect(p.kind).toBe("review");
    expect(p.label).toBe("Review 2 new items");
    expect(p.count).toBe(2);
    expect(p.kick).toBeUndefined();      // the chip flips a filter; it asks nothing
    expect(p.meetingId).toBeUndefined();
  });

  it("says `item`, singular, for one", () => {
    expect(run([], [touched("main"), untouched("a")])[0].label).toBe("Review 1 new item");
  });

  it("counts the same rows the rail's own filter hides — a held meeting with no chat is one of them", () => {
    const ps = run([HELD], [touched("main"), untouched("a")]);
    expect(ps.find((p) => p.kind === "review")?.count).toBe(2);   // the auto chat + the derived held row
  });

  it("a live or upcoming meeting is never hidden, so it is never counted", () => {
    const ps = run([LIVE, SOON], [touched("main")]);
    expect(kinds(ps)).not.toContain("review");
  });

  it("nothing hidden → no chip", () => {
    expect(kinds(run([], [touched("main"), touched("b")]))).not.toContain("review");
  });
});

describe("rule 5 — the workspace has never been set up", () => {
  it("offers setup when the marker is absent, in the person's own words", () => {
    const [p] = proposals([], [touched("main")], false, NOW, "ada@example.com");
    expect(p.kind).toBe("setup");
    expect(p.label).toBe("My email is ada@example.com, set up a workspace for me");
    expect(p.say).toBe(p.label);                      // the chip's words ARE the user's — shown, not hidden
    expect(p.kick).toContain(ONBOARDING_GROUNDING);   // …and they carry the discovery-loop grounding
    expect(p.kick?.endsWith(p.label)).toBe(true);
  });

  it("an unknown address drops the clause rather than printing an empty one", () => {
    const [p] = run([], [touched("main")], false);
    expect(p.label).toBe("Set up a workspace for me");
    expect(p.label).not.toContain("undefined");
    expect(p.say).toBe(p.label);
  });

  it("stays silent when the workspace IS scaffolded", () => {
    expect(kinds(run([], [touched("main")], true))).not.toContain("setup");
  });

  it("stays silent while the probe has not answered — null fails closed", () => {
    expect(kinds(run([], [touched("main")], null))).not.toContain("setup");
  });
});

describe("priority + the cap", () => {
  it("the top three win, in rule order, when everything fires at once", () => {
    const ps = run([LIVE, SOON, HELD], [touched("main"), untouched("a")], false);
    expect(ps).toHaveLength(3);
    expect(kinds(ps)).toEqual(["catch-up", "prep", "outcome"]);
  });

  it("a rule that does not fire promotes the ones below it", () => {
    const ps = run([LIVE, HELD], [touched("main"), untouched("a")], false);
    expect(kinds(ps)).toEqual(["catch-up", "outcome", "review"]);
  });

  it("never more than three, whatever the state", () => {
    expect(run([LIVE, SOON, HELD], [untouched("a"), untouched("b")], false)).toHaveLength(3);
  });

  it("every chip carries a distinct key", () => {
    const ps = run([LIVE, SOON, HELD], [touched("main")], false);
    expect(new Set(ps.map((p) => p.id)).size).toBe(ps.length);
  });
});

describe("the pad", () => {
  it("an account with nothing to say about still gets one chip", () => {
    const ps = run([], [touched("main")]);
    expect(ps).toEqual([GROUP_PROPOSAL]);
    expect(ps[0].kick).toBe(KICK.group);
  });

  it("two rules firing are padded to three", () => {
    const ps = run([LIVE], [touched("main"), untouched("a")]);
    expect(kinds(ps)).toEqual(["catch-up", "review", "group"]);
  });

  it("ONE static, never two — the pad never fills the row on its own", () => {
    expect(kinds(run([], [touched("main")])).filter((k) => k === "group")).toHaveLength(1);
  });

  it("three derived rules leave no room for it", () => {
    expect(kinds(run([LIVE, SOON, HELD], [touched("main")]))).not.toContain("group");
  });
});

// ── what a click DOES ────────────────────────────────────────────────────────────────

/** The handler contract. `proposals()` decides what is OFFERED; `applyProposal()` decides what a
 *  click DOES, and that is the half the founder found broken (2026-09-01): pressing a chip inside a
 *  chat he had just made produced a SECOND chat — "this chat is already new".
 *
 *  So every assertion below is about one thing: the mutation names the chat in front, never a new
 *  one. Rebind · relabel · fire — never append. */
describe("applyProposal — a chip acts in the chat it renders in", () => {
  const NEW = chat({ id: "pchat-1", label: "New chat" });
  const NAMED = chat({ id: "pchat-2", label: "Q3 planning", touched: true });
  const catchUp = () => run([LIVE], [touched("main")])[0];
  const outcome = () => run([HELD], [touched("main")])[0];

  it("the static pad fires IN this chat and names it — no row is minted", () => {
    const e = applyProposal(GROUP_PROPOSAL, NEW, [], NOW);
    expect(e).toEqual({ act: "run", chat: { ...NEW, touched: true, lastActivityAt: NOW, label: "Daily meetings" }, kick: KICK.group, say: undefined });
    expect(e?.act === "run" && e.chat.id).toBe(NEW.id);          // the SAME chat
    expect(e?.act === "run" && e.chat.meeting).toBeUndefined();  // still a plain chat
  });

  it("a chat somebody already named keeps its name", () => {
    const e = applyProposal(GROUP_PROPOSAL, NAMED, [], NOW);
    expect(e?.act === "run" && e.chat.label).toBe("Q3 planning");
    expect(e?.act === "run" && e.chat.touched).toBe(true);
  });

  it("Personal is a name, so the pad does not rewrite it", () => {
    const home = chat({ id: PERSONAL_CHAT_ID, label: "Personal", touched: true });
    const e = applyProposal(GROUP_PROPOSAL, home, [], NOW);
    expect(e).toEqual({ act: "run", chat: { ...home, lastActivityAt: NOW }, kick: KICK.group, say: undefined });
  });

  it("a meeting chip REBINDS this chat to the meeting — same id, meeting title, tabs dropped", () => {
    const e = applyProposal(catchUp(), { ...NEW, artifacts: [{ path: "README.md", label: "personal" }], focus: "|README.md" }, [LIVE], NOW);
    expect(e?.act).toBe("run");
    if (e?.act !== "run") return;
    expect(e.chat.id).toBe(NEW.id);              // the chat in front, not `meet-m-live`
    expect(e.chat.meeting).toBe("m-live");       // …now bound to the meeting
    expect(e.chat.label).toBe("Standup");
    expect(e.chat.touched).toBe(true);
    expect(e.chat.artifacts).toEqual([]);        // so openChat seeds the room's OWN phase pages
    expect(e.chat.focus).toBeUndefined();
    expect(e.kick).toBe(KICK["catch-up"]);
  });

  it("a meeting that already has a chat with history does NOT divert the click into it", () => {
    const other = chat({ id: "meet-m-post", meeting: "m-post", label: "Blue Light Card", touched: true });
    const e = applyProposal(outcome(), NEW, [HELD], NOW);
    expect(e?.act === "run" && e.chat.id).toBe(NEW.id);
    expect(e?.act === "run" && e.chat.id).not.toBe(other.id);
  });

  it("a chat already bound to that meeting has nothing to rebind — it is simply asked", () => {
    const mine = chat({ id: "meet-m-live", meeting: "m-live", label: "Standup" });
    const e = applyProposal(catchUp(), mine, [LIVE], NOW);
    expect(e).toEqual({ act: "run", chat: { ...mine, touched: true, lastActivityAt: NOW }, kick: KICK["catch-up"], say: undefined });
  });

  it("the two structural rows are never rebound — from them a meeting chip opens the meeting's own chat", () => {
    for (const id of [PERSONAL_CHAT_ID, ORG_CHAT_ID]) {
      const e = applyProposal(catchUp(), chat({ id, label: "Personal", touched: true }), [LIVE], NOW);
      expect(e).toEqual({ act: "open", meetingId: "m-live", kick: KICK["catch-up"], say: undefined });
    }
  });

  it("nor is a chat that belongs to a DIFFERENT meeting — its id is that meeting's session", () => {
    const held = chat({ id: "meet-m-post", meeting: "m-post", label: "Blue Light Card" });
    expect(applyProposal(catchUp(), held, [LIVE], NOW)?.act).toBe("open");
  });

  it("a chip for a meeting the list has lost does nothing at all", () => {
    expect(applyProposal(catchUp(), NEW, [], NOW)).toBeNull();
  });

  it("review flips the filter and names no chat — nothing is touched or relabelled", () => {
    const review = run([], [touched("main"), untouched("a")])[0];
    expect(applyProposal(review, NEW, [], NOW)).toEqual({ act: "filter" });
  });

  it("the setup chip speaks in this chat too, and names an unnamed one", () => {
    const p = setupProposal("ada@example.com");
    const e = applyProposal(p, NEW, [], NOW);
    expect(e?.act).toBe("run");
    if (e?.act !== "run") return;
    expect(e.chat.id).toBe(NEW.id);
    expect(e.chat.label).toBe("Workspace setup");
    expect(e.say).toBe("My email is ada@example.com, set up a workspace for me");
    expect(e.kick).toBe(ONBOARDING_GROUNDING + ONBOARDING_REPLY_SEP + e.say);
  });

  it("with no chat in front at all — and only then — a chip may make one", () => {
    expect(applyProposal(GROUP_PROPOSAL, null, [], NOW)).toEqual({ act: "create", label: "Daily meetings", kick: KICK.group, say: undefined });
  });

  it("NOTHING a live row can offer ever appends a chat", () => {
    const offered = proposals([LIVE, SOON, HELD], [touched("main"), untouched("a")], false, NOW, "ada@example.com");
    expect(offered.length).toBeGreaterThan(0);
    for (const p of [...offered, GROUP_PROPOSAL]) {
      const e = applyProposal(p, NEW, [LIVE, SOON, HELD], NOW);
      expect(e?.act).not.toBe("create");
      if (e?.act === "run") expect(e.chat.id).toBe(NEW.id);
    }
  });
});

describe("isUnlabeled — which names a chip may replace", () => {
  it("the placeholders the product itself writes", () => {
    for (const l of ["New chat", "new chat", "Chat", "", "   "]) expect(isUnlabeled(l)).toBe(true);
  });
  it("anything a human or a meeting put there", () => {
    for (const l of ["Personal", "Standup", "Organisation setup", "Chat with Ada"]) expect(isUnlabeled(l)).toBe(false);
  });
});
