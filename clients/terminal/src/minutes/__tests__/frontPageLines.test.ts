/** THE THREE LINES THE FOUNDER WROTE (Vexa-ai/vexa#1634), AS THE HEADER THE DESIGN SPEC ASKED FOR
 *  (Vexa-ai/vexa#1642).
 *
 *  *"what about this one? never spoke about how to make it right, helpful and nice."* — on a strip
 *  reading `Company layer · 25 pages · _global: asks/policies-wizard.md … · dmitry@vexa.ai · 14
 *  minutes ago · Everyone reads, the admin writes · no repo attached · 10+ commits`. The issue then
 *  wrote out what it should say instead, three times, one per viewer, and its design comment of
 *  22:15Z said where each clause goes: an eyebrow, a title, a people row, a last-change row.
 *  **Those are the specification, so this file composes them from fixture data and compares the
 *  strings.**
 *
 *  A screenshot could not hold this. Every claim here is about WORDS — which fact is in the line,
 *  which is not, what stands where a fact is missing — and words are exactly what a rendering test
 *  turns into "something appeared". So the panel is left to `workspaceStrip.test.tsx` and the
 *  sentences are held here, character for character.
 *
 *  Five things are pinned besides the three lines, each of them a rule with a plausible wrong
 *  answer that would still look fine on screen:
 *
 *    · TITLE RESOLUTION — one page reads as *the <title> page*, and an ask as *the … ask*. A path is
 *      never a title, and `asks/policies-wizard.md` was exactly the string the founder was shown.
 *    · THE COUNT FORM — several pages become *five pages*, not five titles and not `+13`.
 *    · NAME RESOLUTION — *you*, else the person's name, else NOTHING (#1642). *someone* used to
 *      stand there and the founder met it on the instance where the person certainly exists; an
 *      address in that position is what made his line read as repository facts before that.
 *    · BUTTONS BY ROLE — a reader gets History and nothing else. Not a greyed control, not one that
 *      explains why it will refuse: none.
 *    · THE HEADER'S OWN SHAPE — the kind is the eyebrow, the README's first heading is the title and
 *      is lifted out of the body, and the people row carries neither.
 */
import { describe, it, expect } from "vitest";
import {
  actInstruction, authorPhrase, avatarPeople, changedThing, companyName, countWord, eyebrow,
  initialsOf, kindFact, lastChangeParts, lastChangeSentence, pageCount, peopleClause, peopleLine,
  policyProfile, splitLeadingH1, stripActs, visibilitySentence, whenPhrase,
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

/** THE PEOPLE ROW, exactly as `WorkspaceReadmePanel` assembles it — the people sentence, the page
 *  count and the one kind pill, in that order, separated by middle dots. Composed here rather than
 *  exported from the module because the row is three ELEMENTS on screen (one of them a pill with an
 *  icon in it) and only its words are this file's business. */
const row = (f: FrontPageFacts): string =>
  [peopleClause(f), pageCount(f.pages), kindFact(f)].filter(Boolean).join(" · ");

// ── the three lines, as the issue wrote them ────────────────────────────────────────────────────
describe("the three viewers' lines", () => {
  it("THE COMPANY LAYER, an admin viewing", () => {
    const facts: FrontPageFacts = {
      kind: "global", name: null, pages: 25, policies: POLICIES,
      company: companyName(README), adminFirstName: "Jane", adminName: "Jane Smith",
      myName: "Jane Smith", members: null, mySubject: "126",
    };

    expect(eyebrow(facts.kind)).toBe("Company layer");
    expect(row(facts)).toBe(
      "everyone at Pilot Industries reads it, Jane writes it · 25 pages · policies: default profile");
    expect(lastChangeSentence(change())).toBe(
      "Jane Smith changed the policies wizard ask 14 minutes ago");
    expect(stripActs({ kind: "global", owner: true }).map((a) => a.label))
      .toEqual(["Set up policies", "Add an editor", "History"]);
    // one face, and it is the person the sentence names — not the person reading
    expect(avatarPeople(facts).map((a) => initialsOf(a.name))).toEqual(["JS"]);
  });

  it("A SHARED WORKSPACE, its owner viewing", () => {
    const facts: FrontPageFacts = {
      kind: "group", name: "Pilot", pages: 9, policies: POLICIES, company: companyName(README),
      mySubject: "126", myRole: "owner", myName: "Alex Roe",
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

    expect(eyebrow(facts.kind)).toBe("Shared workspace");
    expect(facts.name).toBe("Pilot");                       // …and the name is the TITLE, not a clause
    expect(row(facts)).toBe("you, Jane Smith and 2 more · 9 pages");
    expect(lastChangeSentence(board)).toBe("Jane Smith changed the governing board page 2 hours ago");
    expect(stripActs({ kind: "group", owner: true, remote: REMOTE }).map((a) => a.label))
      .toEqual(["Add a member", "Sync", "History"]);
    // you first, then whoever is written down; the two nobody has named are in the "2 more"
    expect(avatarPeople(facts).map((a) => initialsOf(a.name))).toEqual(["AR", "JS"]);
  });

  it("A DESK, its owner viewing", () => {
    const facts: FrontPageFacts = {
      kind: "desk", name: null, pages: 12, policies: POLICIES, company: companyName(README),
      members: null, mySubject: "126", myName: "Jane Smith",
    };
    const now = new Date(2026, 8, 6, 12, 0, 0).getTime();      // local: the claim is calendar days
    const mine = change({
      when: "23 hours ago", ts: Math.floor(new Date(2026, 8, 5, 13, 0, 0).getTime() / 1000),
      kind: "you", author: null, count: 1,
      pages: [{ path: "kg/standing-orders.md", title: "standing orders" }],
    });

    expect(eyebrow(facts.kind)).toBe("Your desk");
    expect(row(facts)).toBe("agents read it for meetings you are in · 12 pages");
    expect(lastChangeSentence(mine, now)).toBe("You changed the standing orders page yesterday");
    expect(stripActs({ kind: "desk", owner: true, remote: null }).map((a) => a.label))
      .toEqual(["Connect a repo", "History"]);
  });
});

const REMOTE = { has_home: true, remote: "origin", url: "https://github.com/pilot/kg",
                 branch: "main", tracked: true, ahead: 2, behind: 0 };

// ── the header's own shape ──────────────────────────────────────────────────────────────────────
describe("the kind is the eyebrow and the README's heading is the title", () => {
  it("lifts the first heading off the body so it is not printed twice", () => {
    const front = splitLeadingH1("# Pilot Industries\n\nWe make the things.\n");
    expect(front.title).toBe("Pilot Industries");
    expect(front.body).toBe("We make the things.\n");
  });

  it("reads past front matter, and leaves a body that has no heading alone", () => {
    const withFm = splitLeadingH1("---\nkind: readme\n---\n\n# Pilot\n\nprose\n");
    expect(withFm.title).toBe("Pilot");
    expect(withFm.body).toBe("---\nkind: readme\n---\n\nprose\n");

    const none = splitLeadingH1("Just prose, no heading.\n\n## A subheading\n");
    expect(none.title).toBeNull();
    expect(none.body).toBe("Just prose, no heading.\n\n## A subheading\n");
    expect(splitLeadingH1(null)).toEqual({ title: null, body: "" });
  });

  it("initials come from the display name, and one name is one letter", () => {
    expect(initialsOf("Jane Smith")).toBe("JS");
    expect(initialsOf("Dmitry")).toBe("D");
    expect(initialsOf("jane-smith")).toBe("JS");
    expect(initialsOf("Ada Byron Lovelace")).toBe("AB");   // two, never three
    expect(initialsOf("")).toBe("");                       // nothing to draw → no circle
  });

  it("draws nobody it cannot name", () => {
    const anon: FrontPageFacts = {
      kind: "group", name: "Pilot", pages: 2, policies: POLICIES, company: null,
      mySubject: "126", members: [{ subject: "77", role: "viewer", email: "jsmith@example.com" }],
    };
    expect(avatarPeople(anon)).toEqual([]);
    expect(avatarPeople({ ...anon, kind: "global", adminName: null })).toEqual([]);
    expect(avatarPeople({ ...anon, kind: "desk", myName: null })).toEqual([]);
  });
});

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

  it("only ONE changed page is a link — a count has nothing to open", () => {
    const one = lastChangeParts(change());
    expect(one.page).toEqual({ path: "asks/policies-wizard.md", title: "policies wizard" });
    const many = lastChangeParts(change({
      count: 2, pages: [{ path: "kg/a.md", title: "a" }, { path: "kg/b.md", title: "b" }],
    }));
    expect(many.page).toBeNull();
    expect(many.thing).toBe("two pages");
  });

  it("a commit that touched no page says the time and the person and stops", () => {
    expect(changedThing({ count: 0, pages: [] })).toBeNull();
    expect(lastChangeSentence(change({ count: 0, pages: [], when: "3 minutes ago", author: "Jane Smith" })))
      .toBe("Jane Smith changed 3 minutes ago");
  });

  it("a workspace nobody has written in says so, rather than saying nothing", () => {
    expect(lastChangeSentence(null)).toBe("Nothing written here yet");
  });
});

// ── who, as a person ────────────────────────────────────────────────────────────────────────────
describe("the author is a person — never an address, and never a pronoun", () => {
  it("you, then their name, then nothing", () => {
    expect(authorPhrase({ kind: "you", author: "Jane Smith" })).toBe("you");
    expect(authorPhrase({ kind: "member", author: "Jane Smith" })).toBe("Jane Smith");
    expect(authorPhrase({ kind: "member", author: null })).toBeNull();
    expect(authorPhrase({ kind: "member", author: "" })).toBeNull();
  });

  it("names NOBODY where nobody could be resolved — *someone* is not a word this line says", () => {
    // Vexa-ai/vexa#1642. The server resolves the person from their ADDRESS now and falls back to
    // that address read as a name, so a null here means there was genuinely nothing to read. The
    // sentence loses its subject rather than gaining a pronoun: *Changed 14 minutes ago: …* is
    // true, and *someone* told the founder the product does not know who works there.
    const line = lastChangeSentence(change({ author: null, when: "14 minutes ago" }));
    expect(line).toBe("Changed the policies wizard ask 14 minutes ago");
    expect(line).not.toContain("someone");
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

  it("drops the writer's clause rather than writing *the admin* in it", () => {
    // Vexa-ai/vexa#1642, the founder's own line: *"everyone at Vexa reads it, THE ADMIN writes
    // it"*, on the deployment whose administrator is himself. A role word in a name's slot reads as
    // a template nobody filled in — so where the name cannot be resolved the clause is not there.
    const said = visibilitySentence("global", POLICIES, { company: "Pilot Industries" });
    expect(said).toBe("everyone at Pilot Industries reads it");
    expect(said).not.toContain("the admin");
  });

  it("says nothing rather than something invented when a fact is missing", () => {
    expect(visibilitySentence("desk", null)).toBeNull();
    expect(visibilitySentence("global", POLICIES, {})).toBe("everyone here reads it");
    // …and a row with a missing clause is a shorter row, never a row with a hole in it
    expect(peopleClause({ kind: "desk", name: null, pages: null, policies: null, company: null,
                          members: null })).toBeNull();
    expect(pageCount(null)).toBeNull();
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
