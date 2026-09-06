/** THE THREE LINES THE FOUNDER WROTE (Vexa-ai/vexa#1634).
 *
 *  *"what about this one? never spoke about how to make it right, helpful and nice."* — on a strip
 *  reading `Company layer · 25 pages · _global: asks/policies-wizard.md … · dmitry@vexa.ai · 14
 *  minutes ago · Everyone reads, the admin writes · no repo attached · 10+ commits`. The issue then
 *  wrote out what it should say instead, three times, one per viewer. **Those three lines are the
 *  specification, so this file renders them from fixture data and compares the strings.**
 *
 *  A screenshot could not hold this. Every claim here is about WORDS — which fact is in the line,
 *  which is not, what stands where a fact is missing — and words are exactly what a rendering test
 *  turns into "something appeared". So the panel is left to `workspaceStrip.test.tsx` and the
 *  sentences are held here, character for character.
 *
 *  Four things are pinned besides the three lines, each of them a rule with a plausible wrong
 *  answer that would still look fine on screen:
 *
 *    · TITLE RESOLUTION — one page reads as *the <title> page*, and an ask as *the … ask*. A path is
 *      never a title, and `asks/policies-wizard.md` was exactly the string the founder was shown.
 *    · THE COUNT FORM — several pages become *five pages*, not five titles and not `+13`.
 *    · NAME RESOLUTION — *you*, else the person's name, else *someone*, and NEVER an address. An
 *      address in that position is what made the founder's line read as repository facts.
 *    · BUTTONS BY ROLE — a reader gets History and nothing else. Not a greyed control, not one that
 *      explains why it will refuse: none.
 */
import { describe, it, expect } from "vitest";
import {
  actInstruction, authorPhrase, changedThing, companyName, countWord, kindFact, lineOne, lineTwo,
  peopleLine, policyProfile, stripActs, visibilitySentence, whenPhrase,
  type FrontPageFacts, type LastChange,
} from "../workspaceFrontPage";

/** `_global/POLICIES.md` as this deployment ships it — the front-matter answers the first line is
 *  derived from, and the profile the company layer's own fact names. */
const POLICIES = `---
kind: policies
profile: default
agent_reads_desk: on
global_admin_only: on
---

# Policies
`;

const README = "# Pilot Industries\n\nWe make the things.\n";

const change = (over: Partial<LastChange> = {}): LastChange => ({
  sha: "7f6b769", msg: "_global: asks/policies-wizard.md — 3 files changed",
  when: "14 minutes ago", ts: 0, kind: "member", author: "Jane Smith",
  pages: [{ path: "asks/policies-wizard.md", title: "policies wizard" }], count: 1,
  ...over,
});

// ── the three lines, as the issue wrote them ────────────────────────────────────────────────────
describe("the three viewers' lines", () => {
  it("THE COMPANY LAYER, an admin viewing", () => {
    const facts: FrontPageFacts = {
      kind: "global", name: null, pages: 25, policies: POLICIES,
      company: companyName(README), adminFirstName: "Jane",
      members: null, mySubject: "126",
    };

    expect(lineOne(facts)).toBe(
      "Company layer · everyone at Pilot Industries reads it, Jane writes it · 25 pages");
    expect(lineTwo(facts, change())).toBe(
      "Changed 14 minutes ago by Jane Smith: the policies wizard ask · policies: default profile");
    expect(stripActs({ kind: "global", owner: true }).map((a) => a.label))
      .toEqual(["Set up policies", "Add an editor", "History"]);
  });

  it("A SHARED WORKSPACE, its owner viewing", () => {
    const facts: FrontPageFacts = {
      kind: "group", name: "Pilot", pages: 9, policies: POLICIES, company: companyName(README),
      mySubject: "126", myRole: "owner",
      members: [
        { subject: "126", role: "owner", email: "jsmith@example.com" },
        { subject: "77", role: "contributor", name: "Jane Smith" },
        { subject: "78", role: "viewer" },
        { subject: "79", role: "viewer" },
      ],
    };
    const board = change({
      when: "2 hours ago", author: "Jane Smith", count: 1,
      pages: [{ path: "kg/board.md", title: "the governing board" }],
    });

    expect(lineOne(facts)).toBe("Pilot · shared workspace · you, Jane Smith and 2 more");
    expect(lineTwo(facts, board)).toBe("Changed 2 hours ago by Jane Smith: the governing board page");
    expect(stripActs({ kind: "group", owner: true, remote: REMOTE }).map((a) => a.label))
      .toEqual(["Add a member", "Sync", "History"]);
  });

  it("A DESK, its owner viewing", () => {
    const facts: FrontPageFacts = {
      kind: "desk", name: null, pages: 12, policies: POLICIES, company: companyName(README),
      members: null, mySubject: "126",
    };
    const now = new Date(2026, 8, 6, 12, 0, 0).getTime();      // local: the claim is calendar days
    const mine = change({
      when: "23 hours ago", ts: Math.floor(new Date(2026, 8, 5, 13, 0, 0).getTime() / 1000),
      kind: "you", author: null, count: 1,
      pages: [{ path: "kg/standing-orders.md", title: "standing orders" }],
    });

    expect(lineOne(facts)).toBe("Your desk · 12 pages · agents read it for meetings you are in");
    expect(lineTwo(facts, mine, now)).toBe("Changed yesterday by you: the standing orders page");
    expect(stripActs({ kind: "desk", owner: true, remote: null }).map((a) => a.label))
      .toEqual(["Connect a repo", "History"]);
  });
});

const REMOTE = { has_home: true, remote: "origin", url: "https://github.com/pilot/kg",
                 branch: "main", tracked: true, ahead: 2, behind: 0 };

// ── the changed thing, by its title ─────────────────────────────────────────────────────────────
describe("the changed thing is named, never pathed", () => {
  it("one page reads as the page, one ask as the ask", () => {
    expect(changedThing({ count: 1, pages: [{ path: "kg/board.md", title: "the governing board" }] }))
      .toBe("the governing board page");
    expect(changedThing({ count: 1, pages: [{ path: "asks/policies-wizard.md", title: "policies wizard" }] }))
      .toBe("the policies wizard ask");
  });

  it("several pages are a COUNT — not a list, and not the commit's `+13`", () => {
    const pages = Array.from({ length: 5 }, (_, i) => ({ path: `kg/p${i}.md`, title: `page ${i}` }));
    expect(changedThing({ count: 5, pages })).toBe("five pages");
    expect(countWord(2)).toBe("two");
    expect(countWord(10)).toBe("ten");
    expect(countWord(17)).toBe("17");                 // a number pretending to be a word is worse
  });

  it("a commit that touched no page says the time and the person and stops", () => {
    expect(changedThing({ count: 0, pages: [] })).toBeNull();
    expect(lineTwo({ kind: "desk", name: null, pages: 3, policies: null, company: null,
                     members: null },
                   change({ count: 0, pages: [], when: "3 minutes ago", author: "Jane Smith" })))
      .toBe("Changed 3 minutes ago by Jane Smith");
  });

  it("a workspace nobody has written in says so, rather than saying nothing", () => {
    expect(lineTwo({ kind: "group", name: "Pilot", pages: 0, policies: null, company: null,
                     members: [] }, null))
      .toBe("Nothing has been written here yet");
  });
});

// ── who, as a person ────────────────────────────────────────────────────────────────────────────
describe("the author is a person, never an address", () => {
  it("you, then their name, then someone", () => {
    expect(authorPhrase({ kind: "you", author: "Jane Smith" })).toBe("you");
    expect(authorPhrase({ kind: "member", author: "Jane Smith" })).toBe("Jane Smith");
    expect(authorPhrase({ kind: "member", author: null })).toBe("someone");
    expect(authorPhrase({ kind: "member", author: "" })).toBe("someone");
  });

  it("*someone* is what stands where nobody has been written down", () => {
    // The server answers `null` rather than the address it can see, and this is the word that takes
    // its place: `Changed 14 minutes ago by jsmith@example.com` is the line #1634 was opened about.
    const line = lineTwo({ kind: "group", name: "Pilot", pages: 1, policies: null, company: null,
                           members: [] },
                          change({ author: null, when: "14 minutes ago" }));
    expect(line).toBe("Changed 14 minutes ago by someone: the policies wizard ask");
    expect(line).not.toContain("@");
  });

  it("git's own relative time stands, except where a person would say yesterday", () => {
    // Local dates, deliberately: the substitution is about CALENDAR days, so a fixture written in
    // UTC would pass or fail depending on where the machine running it happens to be.
    const now = new Date(2026, 8, 6, 9, 0, 0).getTime();
    const at = (...d: [number, number, number, number, number]) =>
      Math.floor(new Date(d[0], d[1], d[2], d[3], d[4]).getTime() / 1000);
    expect(whenPhrase({ when: "23 hours ago", ts: at(2026, 8, 5, 23, 0) }, now)).toBe("yesterday");
    expect(whenPhrase({ when: "2 hours ago", ts: at(2026, 8, 6, 7, 0) }, now)).toBe("2 hours ago");
    expect(whenPhrase({ when: "3 days ago", ts: at(2026, 8, 3, 9, 0) }, now)).toBe("3 days ago");
    expect(whenPhrase({ when: "5 minutes ago" }, now)).toBe("5 minutes ago");   // no stamp, no guess
  });
});

// ── who is here ─────────────────────────────────────────────────────────────────────────────────
describe("people as names, you first", () => {
  const m = (subject: string, name?: string) => ({ subject, role: "viewer", name });

  it("shapes the sentence to how many there are", () => {
    expect(peopleLine([m("126")], "126")).toBe("just you");
    expect(peopleLine([m("126"), m("77", "Jane Smith")], "126")).toBe("you and Jane Smith");
    expect(peopleLine([m("126"), m("77", "Jane Smith"), m("78", "John Doe")], "126"))
      .toBe("you, Jane Smith and John Doe");
    expect(peopleLine([m("126"), m("77", "Jane Smith"), m("78", "John Doe"), m("79", "Ada Byron")], "126"))
      .toBe("you, Jane Smith, John Doe and 1 more");
  });

  it("counts the people nobody has named rather than printing their addresses", () => {
    const roster = [m("126"), m("77", "Jane Smith"),
                    { subject: "78", role: "viewer", email: "jsmith@example.com" },
                    { subject: "79", role: "viewer", email: "jdoe@example.com" }];
    const said = peopleLine(roster, "126");
    expect(said).toBe("you, Jane Smith and 2 more");
    expect(said).not.toContain("@");
  });

  it("says so honestly when the roster is not this reader's to read", () => {
    // A reader of a group may not list the members — that is the design, not a failure.
    expect(peopleLine(null, "126")).toBe("you and the other members");
  });
});

// ── the visibility sentence, derived ────────────────────────────────────────────────────────────
describe("where people are not the point, the rule is", () => {
  it("is derived from POLICIES.md's answers and the kind, never retyped", () => {
    expect(visibilitySentence("desk", POLICIES)).toBe("agents read it for meetings you are in");
    expect(visibilitySentence("desk", POLICIES.replace("agent_reads_desk: on", "agent_reads_desk: off")))
      .toBe("no agent reads it");
    expect(visibilitySentence("global", POLICIES, { company: "Pilot Industries", adminFirstName: "Jane" }))
      .toBe("everyone at Pilot Industries reads it, Jane writes it");
    expect(visibilitySentence("group", POLICIES)).toBeNull();     // a group's people ARE the point
  });

  it("says nothing rather than something invented when a fact is missing", () => {
    expect(visibilitySentence("desk", null)).toBeNull();
    expect(visibilitySentence("global", POLICIES, {}))
      .toBe("everyone here reads it, the admin writes it");
    // …and a line with a missing clause is a shorter line, never a line with a hole in it
    expect(lineOne({ kind: "desk", name: null, pages: null, policies: null, company: null,
                     members: null })).toBe("Your desk");
  });

  it("refuses the placeholder headings the setup conversation writes while it is still asking", () => {
    expect(companyName("# Pilot Industries\n")).toBe("Pilot Industries");
    expect(companyName("# Company\n")).toBeNull();
    expect(companyName("Some prose first\n\n# Pilot Industries\n")).toBeNull();
    expect(companyName(null)).toBeNull();
  });
});

// ── the kind-specific fact ──────────────────────────────────────────────────────────────────────
describe("one fact per kind, and only where there is one", () => {
  it("the policy profile on the company layer, the bound series on a group, nothing on a desk", () => {
    const base = { name: "Pilot", pages: 1, company: null, members: null };
    expect(policyProfile(POLICIES)).toBe("default");
    expect(kindFact({ ...base, kind: "global", policies: POLICIES })).toBe("policies: default profile");
    expect(kindFact({ ...base, kind: "global", policies: "# no front matter\n" })).toBeNull();
    expect(kindFact({ ...base, kind: "group", policies: POLICIES,
                      bound: [{ key: "cal:u1", title: "the Tuesday sync", recurring: true, runs: 3, latest: "" }] }))
      .toBe("bound to the Tuesday sync");
    expect(kindFact({ ...base, kind: "group", policies: POLICIES, bound: [] })).toBeNull();
    expect(kindFact({ ...base, kind: "desk", policies: POLICIES })).toBeNull();
  });
});

// ── the acts ────────────────────────────────────────────────────────────────────────────────────
describe("the buttons are what this viewer may do, and nothing else", () => {
  it("a reader gets History and not one other control", () => {
    expect(stripActs({ kind: "group", owner: false, remote: REMOTE }).map((a) => a.id)).toEqual(["history"]);
    expect(stripActs({ kind: "global", owner: false }).map((a) => a.id)).toEqual(["history"]);
    expect(stripActs({ kind: "desk", owner: false }).map((a) => a.id)).toEqual(["history"]);
  });

  it("an owner gets Sync when a repo is attached and Connect a repo when none is", () => {
    expect(stripActs({ kind: "group", owner: true, remote: REMOTE }).map((a) => a.id))
      .toEqual(["member", "sync", "history"]);
    expect(stripActs({ kind: "group", owner: true, remote: { ...REMOTE, has_home: false } }).map((a) => a.id))
      .toEqual(["member", "connect", "history"]);
  });

  it("the company layer offers no repo button — it is not one of that flow's targets", () => {
    expect(stripActs({ kind: "global", owner: true, remote: REMOTE }).map((a) => a.id))
      .toEqual(["policies", "editor", "history"]);
  });

  it("a desk has no members to add", () => {
    expect(stripActs({ kind: "desk", owner: true, remote: REMOTE }).map((a) => a.id))
      .toEqual(["sync", "history"]);
  });

  it("every act with no verb behind it asks the CHAT, in one question, and waits for a yes", () => {
    const where = { workspace: "pilot-b5e60c", name: "Pilot" };
    for (const id of ["sync", "connect"] as const) {
      const said = actInstruction(id, where);
      expect(said).toContain("Pilot");
      expect(said.toLowerCase()).toMatch(/ask me/);
    }
    // #1632's principle in the string itself: no form, one question, a yes before anything happens
    expect(actInstruction("editor", where)).toContain("No form: ask me here.");
    expect(actInstruction("editor", where)).toContain("only write the file if I say yes");
    // …and the acts that ARE typed intents compose nothing here: `member_add` (#1632) and
    // `policies_wizard` (#1627) name a kind the server maps to an ask, because a client that could
    // compose an act's words could drive somebody else's agent. History is a disclosure, not a turn.
    expect(actInstruction("member", where)).toBe("");
    expect(actInstruction("policies", where)).toBe("");
    expect(actInstruction("history", where)).toBe("");
  });
});
