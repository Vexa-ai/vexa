/** The empty chat's proposal chips, tested at the ONE function that decides them.
 *
 *  `proposals()` is pure — meetings in, chats in, the desk's facts and its short list in, chips out
 *  — so every rule, the priority between them and the cap are decidable here without a browser, a
 *  backend or a real clock. That is the whole reason the rule lives in a function instead of in JSX.
 *
 *  Covered: each derived rule alone · the priority order when they collide · the TEN-chip cap
 *  (Vexa-ai/vexa#1614: *"can have up to 10 items"*) · the two ways a rule declines to fire (an
 *  unknown desk, a meeting already written about) · the items other agents wrote, with their
 *  sources · the two STANDING acts, which the cap never crowds out and whose Meet half has a
 *  "connect Google" branch because nothing here can create a Meet yet · and that NOTHING is padded
 *  in behind the rules (F36 — the standing "Create a group for daily meetings" suggestion is
 *  deleted; a button is a scaffolded intent, not a default). */
import { describe, expect, it } from "vitest";
import type { MeetingMock } from "../../surfaces/meetingModel";
import type { DeskProposal } from "../../surfaces/proposalsApi";
import type { DeskFacts } from "../../surfaces/workspaceApi";
import type { Chat } from "../chats";
import { applyProposal, isUnlabeled, jtbdProposal, KICK, needsSetup, PREP_WINDOW_MS, PROPOSALS_MAX, proposals, setupProposal, standingProposals } from "../proposals";
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../../canvas/actions";

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

/** The desk states rule 5 turns on, named once (Vexa-ai/vexa#1613). */
const BLANK: DeskFacts = { state: "new", scaffolded: false };     // nothing has ever been written
const WORKED_IN: DeskFacts = { state: "warm", scaffolded: false }; // THE FOUNDER'S CASE, 2026-09-06
const A_PILE: DeskFacts = { state: "pile", scaffolded: false };    // reports landed, nobody wired
const FINISHED: DeskFacts = { state: "warm", scaffolded: true };   // a setup conversation completed

const run = (m: MeetingMock[], c: Chat[], desk: DeskFacts | null = FINISHED) => proposals(m, c, desk, NOW);
const labels = (ps: { label: string }[]) => ps.map((p) => p.label);
const kinds = (ps: { kind: string }[]) => ps.map((p) => p.kind);
/** THE TWO STANDING ACTS ARE ALWAYS AT THE END (#1614), so the rules above are read without them.
 *  They get their own describe below; every other test here is about what this account makes true. */
const STANDING_KINDS = ["meet", "link"];
const derived = <T extends { kind: string }>(ps: T[]): T[] =>
  ps.filter((p) => !STANDING_KINDS.includes(p.kind));

/** One row of the desk's short list, as the store hands it over. */
const item = (id: string, act: string, label = "Pilot sync"): DeskProposal =>
  ({ id, source: `meeting:${id}`, source_label: label, act, since: "2026-09-01T09:00:00Z",
     status: "open", by: "post-meeting" });

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
    const [p] = proposals([], [touched("main")], BLANK, NOW, "ada@example.com");
    expect(p.kind).toBe("setup");
    expect(p.label).toBe("My email is ada@example.com, set up a workspace for me");
    expect(p.say).toBe(p.label);                      // the chip's words ARE the user's — shown, not hidden
    expect(p.kick).toContain(ONBOARDING_GROUNDING);   // …and they carry the discovery-loop grounding
    expect(p.kick?.endsWith(p.label)).toBe(true);
  });

  it("an unknown address drops the clause rather than printing an empty one", () => {
    const [p] = run([], [touched("main")], BLANK);
    expect(p.label).toBe("Set up a workspace for me");
    expect(p.label).not.toContain("undefined");
    expect(p.say).toBe(p.label);
  });

  it("stays silent when a setup conversation has finished", () => {
    expect(kinds(run([], [touched("main")], FINISHED))).not.toContain("setup");
  });

  it("stays silent while the probe has not answered — null fails closed", () => {
    expect(kinds(run([], [touched("main")], null))).not.toContain("setup");
  });

  // ── THE DEFECT (Vexa-ai/vexa#1613) ────────────────────────────────────────────────────────────
  //
  //  Founder, 2026-09-06 14:10Z: a brand-new chat offered him *"My email is dmitry@vexa.ai, set up
  //  a workspace for me"* over a desk that had existed since 13:30 and already held company,
  //  person and project entities. The rule was reading `.scaffolded` — a marker ONE route writes
  //  (the personal onboarding conversation, as its final act) and which `flows_defs/production.py`
  //  describes in as many words as *"a harmless marker; it gates nothing"*. Its absence had stopped
  //  meaning "never set up" the moment a desk acquired other ways to come into existence.

  it("stays silent over a desk somebody has WORKED IN, marker or no marker", () => {
    expect(kinds(run([], [touched("main")], WORKED_IN))).not.toContain("setup");
    expect(needsSetup(WORKED_IN)).toBe(false);
  });

  it("stays silent over a desk that meeting reports have landed in", () => {
    // `pile` is a desk nobody has TALKED to — but something is written there, so offering to set
    // it up is still the same lie.
    expect(kinds(run([], [touched("main")], A_PILE))).not.toContain("setup");
    expect(needsSetup(A_PILE)).toBe(false);
  });

  it("the derivation, in one place: only a desk with nothing in it is offered setup", () => {
    expect(needsSetup(BLANK)).toBe(true);
    expect(needsSetup(FINISHED)).toBe(false);
    expect(needsSetup({ state: "new", scaffolded: true })).toBe(false);  // marker wins on its own
    expect(needsSetup(null)).toBe(false);                                // not known yet → offer nothing
  });
});

describe("priority + the cap", () => {
  it("the derived rules come first, in rule order, when everything fires at once", () => {
    // ALL of them now that the row holds ten (#1614). Under the old three-chip cap `review` and the
    // setup chip were cut here — which is exactly the thing a cap should not silently decide.
    const ps = derived(run([LIVE, SOON, HELD], [touched("main"), untouched("a")], BLANK));
    expect(kinds(ps)).toEqual(["catch-up", "prep", "outcome", "review", "setup"]);
  });

  it("a rule that does not fire promotes the ones below it", () => {
    const ps = derived(run([LIVE, HELD], [touched("main"), untouched("a")], FINISHED));
    expect(kinds(ps)).toEqual(["catch-up", "outcome", "review"]);
  });

  it("never more than ten, whatever the state", () => {
    const desk = Array.from({ length: 12 }, (_, n) => item(`i${n}`, `Job ${n}`));
    const ps = proposals([LIVE, SOON, HELD], [untouched("a"), untouched("b")], BLANK, NOW, null, desk);
    expect(ps).toHaveLength(PROPOSALS_MAX);
  });

  it("the standing acts SURVIVE the cap — that is what standing means", () => {
    const desk = Array.from({ length: 30 }, (_, n) => item(`i${n}`, `Job ${n}`));
    const ps = proposals([LIVE, SOON, HELD], [untouched("a")], BLANK, NOW, null, desk);
    expect(ps).toHaveLength(PROPOSALS_MAX);
    expect(kinds(ps).slice(-2)).toEqual(["meet", "link"]);
  });

  it("every chip carries a distinct key", () => {
    const desk = [item("a", "The migration doc"), item("b", "Brief the board")];
    const ps = proposals([LIVE, SOON, HELD], [touched("main")], BLANK, NOW, null, desk);
    expect(new Set(ps.map((p) => p.id)).size).toBe(ps.length);
  });
});

describe("rule 6 — the short list other agents wrote", () => {
  const DESK = [item("a", "The migration doc, by Friday"), item("b", "Brief the board", "TSC")];
  const withDesk = (d: DeskProposal[]) => proposals([], [touched("main")], FINISHED, NOW, null, d);

  it("offers each item as its own chip, in the order the store gave them", () => {
    expect(labels(derived(withDesk(DESK)))).toEqual(["The migration doc, by Friday", "Brief the board"]);
  });

  it("a chip says WHERE the job came from — an item somebody else wrote has to", () => {
    const [p] = derived(withDesk(DESK));
    expect(p.kind).toBe("jtbd");
    expect(p.source).toBe("Pilot sync");
    expect(p.itemId).toBe("a");                        // …and names the row a click or a x closes
  });

  it("the act is what the person SAYS, and the kick carries the source and read-first", () => {
    const p = jtbdProposal(item("a", "The migration doc"));
    expect(p.say).toBe("The migration doc");           // shown as their own words, not as machinery
    expect(p.kick).toContain("The migration doc");
    expect(p.kick).toContain("Pilot sync");
    expect(p.kick).toContain("Read what exists first");
  });

  it("an item whose source has no name still says where it came from", () => {
    const p = jtbdProposal({ id: "z", source: "page:kg/entities/company/oenb.md", act: "Find a source" });
    expect(p.kick).toContain("page:kg/entities/company/oenb.md");
  });

  it("an empty list changes nothing — the row is what it was before the store existed", () => {
    expect(derived(withDesk([]))).toEqual([]);
  });
});

describe("the standing acts — always there", () => {
  it("both of them close every row, whatever this account looks like", () => {
    for (const c of [[], [touched("main")], [untouched("a"), untouched("b")]]) {
      for (const m of [[], [LIVE], [LIVE, SOON, HELD]]) {
        expect(kinds(proposals(m, c, null, NOW)).slice(-2)).toEqual(["meet", "link"]);
      }
    }
  });

  it("with Google connected the act creates the Meet and sends the bot in one go", () => {
    const [meet] = standingProposals(true);
    expect(meet.label).toBe("Create a Google Meet and put Vexa in it");
    expect(meet.kick).toContain("send the Vexa bot into it");
  });

  it("without it the act is CONNECT GOOGLE, said plainly — never a Meet it cannot make", () => {
    const [meet] = standingProposals(false);
    expect(meet.label).toBe("Connect Google, so I can create meetings for you");
    expect(meet.label).not.toContain("Create a Google Meet");
    expect(meet.kick).toContain("Tell me what is missing");
  });

  it("the deployment default is the honest branch — nothing here can create a Meet yet", () => {
    expect(labels(proposals([], [], null, NOW))).toContain("Connect Google, so I can create meetings for you");
  });

  it("pasting a link is the act that already works", () => {
    const link = standingProposals(false)[1];
    expect(link.label).toBe("Paste a meeting link");
    expect(link.say).toContain("I'll paste the link");
  });
});

describe("F36 — nothing is padded in behind the rules", () => {
  it("an account with nothing to say about is offered nothing but the standing acts", () => {
    // It used to be offered "Create a group for daily meetings" — a suggestion that appeared
    // because the row looked short. The founder met it under a chat he had never created. What
    // stands here now stands for a different reason: #1614 asks for those two by name.
    expect(derived(run([], [touched("main")]))).toEqual([]);
  });

  it("two rules firing stay two — the row is not filled up", () => {
    expect(kinds(derived(run([LIVE], [touched("main"), untouched("a")])))).toEqual(["catch-up", "review"]);
  });

  it("every chip that IS offered comes from live state or is standing, never from a pad", () => {
    // the whole offered set, over a rich account: each derived kind is produced by a rule that read
    // something real (a meeting's phase, the rail's hidden count, the desk's own state, the store).
    const ps = proposals([LIVE, SOON, HELD], [touched("main"), untouched("a")], BLANK, NOW,
                         "ada@example.com", [item("a", "The migration doc")]);
    for (const p of ps) {
      expect(["catch-up", "prep", "outcome", "review", "setup", "jtbd", "meet", "link"]).toContain(p.kind);
    }
  });

  it("the deleted suggestion is not reachable under any state", () => {
    for (const c of [[], [touched("main")], [untouched("a")]]) {
      for (const m of [[], [LIVE], [HELD], [LIVE, SOON, HELD]]) {
        for (const sc of [FINISHED, BLANK, WORKED_IN, A_PILE, null]) {
          expect(labels(proposals(m, c, sc, NOW))).not.toContain("Create a group for daily meetings");
        }
      }
    }
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

  it("a non-meeting chip fires IN this chat and names it — no row is minted", () => {
    const p = setupProposal("ada@example.com");
    const e = applyProposal(p, NEW, [], NOW);
    expect(e?.act === "run" && e.chat.id).toBe(NEW.id);          // the SAME chat
    expect(e?.act === "run" && e.chat.label).toBe("Workspace setup");
    expect(e?.act === "run" && e.chat.meeting).toBeUndefined();  // still a plain chat
  });

  it("a chat somebody already named keeps its name", () => {
    const e = applyProposal(setupProposal(null), NAMED, [], NOW);
    expect(e?.act === "run" && e.chat.label).toBe("Q3 planning");
    expect(e?.act === "run" && e.chat.touched).toBe(true);
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

  // (The two SEEDED rows — `main` and `org-setup` — used to be exempt from rebinding, because
  // turning "Personal" into a meeting's chat would have retired the home row for good. F34 deleted
  // the seeding and `pruneStale` deletes the rows, so there is nothing left to exempt; the rule
  // that survives is the one below, about a chat bound to a DIFFERENT meeting.)

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
    const p = setupProposal(null);
    expect(applyProposal(p, null, [], NOW)).toEqual({ act: "create", label: "Workspace setup", kick: p.kick, say: p.say });
  });

  it("an item another agent wrote fires IN this chat, as the person's own words", () => {
    const e = applyProposal(jtbdProposal(item("a", "The migration doc")), NEW, [], NOW);
    expect(e?.act).toBe("run");
    if (e?.act !== "run") return;
    expect(e.chat.id).toBe(NEW.id);
    expect(e.say).toBe("The migration doc");
    expect(e.kick).toContain("Pilot sync");
  });

  it("NOTHING a live row can offer ever appends a chat", () => {
    const offered = proposals([LIVE, SOON, HELD], [touched("main"), untouched("a")], BLANK, NOW,
                              "ada@example.com", [item("a", "The migration doc")]);
    expect(offered.length).toBeGreaterThan(0);
    for (const p of [...offered, setupProposal("ada@example.com")]) {
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
