/** MEETING PREP, READ FROM ITS OWNERS (`meeting_prep`, validation log O2). The pure half: the goal on the
 *  meeting page's front matter (read and written without touching anything else), the agenda and the
 *  material on the page, the people resolved per viewer by address (never a handle as a name), the
 *  previous occurrence and its open cards, and readiness derived at read time. */
import { describe, expect, it } from "vitest";
import {
  cardAboutMeeting, openFromLast, prepPeople, previousOccurrence, readAgenda, readGoal, readMaterial,
  readiness, sourcesOf, startsIn, writeGoal,
} from "../prepRead";

const PAGE = [
  "---", "type: meeting", "meeting: 201", "title: Weekly sync", "# kept comment", "---", "",
  "# Weekly sync", "", "## Agenda", "", "1. Test plan status", "- [ ] Rollout dates", "", "## Material", "",
  "See [[page:pilot/plans/test-plan.md|Test plan]] and [[file:pilot/data.csv]].", "Again [[page:pilot/plans/test-plan.md|Test plan]].",
  "With [[person:ana@x.example]] and [[meeting:200]].", "",
].join("\n");

describe("the goal lives in the page's front matter", () => {
  it("reads nothing when none is set, and the line when one is", () => {
    expect(readGoal(PAGE)).toBe("");
    expect(readGoal("---\ngoals: Agree the rollout date\n---\n")).toBe("Agree the rollout date");
    expect(readGoal("---\ngoals:\n  - one\n  - two\n---\n")).toBe("one · two");
    expect(readGoal("no front matter")).toBe("");
  });
  it("writes goals: and leaves every other key, the comment and the body exactly as they were", () => {
    const next = writeGoal(PAGE, "  Agree the rollout date ");
    expect(readGoal(next)).toBe("Agree the rollout date");
    expect(next).toContain("# kept comment");
    expect(next).toContain("meeting: 201");
    expect(next.slice(next.indexOf("# Weekly sync"))).toBe(PAGE.slice(PAGE.indexOf("# Weekly sync")));
    expect(readGoal(writeGoal(next, "Changed"))).toBe("Changed");
  });
  it("gives a page with no front matter one", () => {
    const next = writeGoal("# Notes\n", "Goal");
    expect(next.startsWith("---\n")).toBe(true);
    expect(readGoal(next)).toBe("Goal");
    expect(next.endsWith("# Notes\n")).toBe(true);
  });
});

describe("agenda and material are read off the page", () => {
  it("reads the Agenda section's items, numbered, bulleted or ticked", () => {
    expect(readAgenda(PAGE)).toEqual(["Test plan status", "Rollout dates"]);
    expect(readAgenda("---\nagenda: [A, B]\n---\n")).toEqual(["A", "B"]);
    expect(readAgenda("# none\n- x\n")).toEqual([]);
  });
  it("reads each object reference once — a person or a meeting is not material", () => {
    expect(readMaterial(PAGE)).toEqual(["[[page:pilot/plans/test-plan.md|Test plan]]", "[[file:pilot/data.csv]]"]);
  });
});

describe("people, as this viewer knows them", () => {
  const known = [
    { key: "a:7", subject: "7", name: "Ana Pereira", email: "Ana@x.example", how: ["met", "chat", "workspace"], headline: "Head of data" },
    { key: "e:b", subject: null, name: "Bo Lind", email: "bo@y.example", how: ["mail", "page"] },
  ];
  it("resolves attendees by address, leaves the viewer out, once each, sources in words", () => {
    const ps = prepPeople([
      { email: "ana@x.example", name: "ana@x.example" }, { email: "me@v.example" }, { email: "ANA@x.example" },
      { email: "bo@y.example" }, { email: "carl.jung@z.example" },
    ], known, { email: "me@v.example" });
    expect(ps.map((p) => p.name)).toEqual(["Ana Pereira", "Bo Lind", "Carl Jung"]);
    expect(ps[0]).toMatchObject({ role: "Head of data", sources: ["met before", "chats", "shared workspace"], known: true, subject: "7" });
    expect(ps[1].sources).toEqual(["email", "your page"]);
    // nobody knows Carl yet: the invite's address gives the name, never a handle; no sources
    expect(ps[2]).toMatchObject({ known: false, sources: [], subject: null });
  });
  it("words only the sources it knows", () => {
    expect(sourcesOf(["met", "met", "weird"])).toEqual(["met before"]);
  });
});

describe("open from last time", () => {
  const rows = [
    { id: "199", title: "Weekly sync", scheduled_at: "2026-10-04T13:00:00Z" },
    { id: "200", title: "Weekly sync", scheduled_at: "2026-10-11T13:00:00Z", native_id: "abc-defg-hij" },
    { id: "150", title: "Other", scheduled_at: "2026-10-12T13:00:00Z" },
    { id: "201", title: "Weekly sync", scheduled_at: "2026-10-18T13:00:00Z", live_status: "scheduled" },
  ];
  it("the previous occurrence is the newest earlier row with the same title", () => {
    expect(previousOccurrence(rows, rows[3])?.id).toBe("200");
    expect(previousOccurrence(rows, rows[0])).toBeNull();
  });
  it("lists the open cards about it, by row id, native id or full reference", () => {
    const prev = rows[1];
    const cards = [
      { id: "c1", title: "Send the test plan", about: { kind: "meeting", ref: "200" } },
      { id: "c2", title: "Done already", open: false, about: { kind: "meeting", ref: "200" } },
      { id: "c3", title: "By native", about: { kind: "meeting", ref: "abc-defg-hij" } },
      { id: "c4", title: "By reference", about: { kind: "meeting", ref: "[[meeting:200|Weekly sync]]" } },
      { id: "c5", title: "Other meeting", about: { kind: "meeting", ref: "199" } },
      { id: "c6", title: "A page", about: { kind: "page", ref: "200" } },
    ];
    expect(openFromLast(cards, prev).map((c) => c.id)).toEqual(["c1", "c3", "c4"]);
    expect(openFromLast(cards, null)).toEqual([]);
    expect(cardAboutMeeting({ id: "x", title: "", about: null }, prev)).toBe(false);
  });
});

describe("readiness is derived, never stored", () => {
  it("needs a goal, someone known and material", () => {
    const known = [{ name: "A", email: "a@x", subject: "1", key: "k", role: "", sources: [], known: true }];
    expect(readiness("g", known, ["[[page:x]]"]).ready).toBe(true);
    expect(readiness("", known, ["[[page:x]]"])).toMatchObject({ goal: false, ready: false });
    expect(readiness("g", [{ ...known[0], known: false }], ["m"]).people).toBe(false);
  });
});

describe("when, as Home says it", () => {
  const now = Date.UTC(2026, 9, 18, 12, 0);
  it("minutes, hours, then day and time", () => {
    expect(startsIn(now + 30 * 60_000, now)).toBe("in 30 min");
    expect(startsIn(now + 2 * 3600_000, now)).toBe("in 2 h");
    expect(startsIn(now + 3 * 86_400_000, now)).toMatch(/\d{2}:\d{2}$/);
    expect(startsIn(now - 1, now)).toBe("now");
  });
});
