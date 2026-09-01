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
import { GROUP_PROPOSAL, KICK, PREP_WINDOW_MS, proposals } from "../proposals";

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
  it("offers setup when the marker is absent", () => {
    const [p] = run([], [touched("main")], false);
    expect(p.kind).toBe("setup");
    expect(p.label).toBe("Set up my workspace");
    expect(p.kick).toBeUndefined();      // the personal setup path writes its own opening turn
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
