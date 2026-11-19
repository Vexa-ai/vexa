/** The empty chat's suggestion row, tested at the ONE function that decides it.
 *
 *  Founder ruling, 2026-10-11: *"they might want to connect email, calendar and if connected is to
 *  check email and prepare for next meeting."* `proposals()` is pure — the person's connections, their
 *  upcoming calendar events, the desk's facts and short list in; chips out — so every state is
 *  decidable here without a browser, a broker or a real clock.
 *
 *  Covered, one describe per state: none connected · email only · calendar only · both, with and
 *  without an upcoming meeting · the connections status unreadable · not answered yet. Then: the
 *  meeting chip never carries a placeholder name; the removed chips are gone; what a click does. */
import { describe, expect, it } from "vitest";
import type { DeskProposal } from "../../surfaces/proposalsApi";
import type { DeskFacts } from "../../data/workspaceApi";
import type { Chat } from "../chats";
import {
  applyProposal, atTime, CHECK_EMAIL, SHARED_WORKSPACE, isUnlabeled, jtbdProposal, needsSetup, nextMeeting, PROPOSALS_MAX,
  proposals, setupProposal, UPCOMING_WINDOW_MS, CALL_VEXA, type ConnectionRow, type UpcomingEvent,
} from "../proposals";
import { ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../../platform";

const NOW = new Date(2026, 9, 11, 12, 0, 0).getTime();   // a fixed LOCAL noon; nothing reads the clock
const inMins = (mins: number) => new Date(NOW + mins * 60000).toISOString();

const row = (provider: string, status = "ready", id = provider.padEnd(32, "0").slice(0, 32)): ConnectionRow =>
  ({ id, provider, status, label: provider });
const GMAIL = row("google_email");
const CAL = row("google_calendar");
const NONE: ConnectionRow[] = [];
const EMAIL_ONLY = [GMAIL];
const CAL_ONLY = [CAL];
const BOTH = [GMAIL, CAL];

const ev = (title: string, mins: number | null): UpcomingEvent => ({ title, start: mins === null ? null : inMins(mins) });
const PRICING = ev("Acme — pricing review", 75);

const labels = (ps: { label: string }[]) => ps.map((p) => p.label);
const kinds = (ps: { kind: string }[]) => ps.map((p) => p.kind);

const FINISHED: DeskFacts = { state: "warm", scaffolded: true };
const BLANK: DeskFacts = { state: "new", scaffolded: false };
/** The call with Vexa (it replaced the voice note, founder 2026-10-14) needs no connection: once the row is decided it is always offered, after the
 *  connection chips (founder 2026-10-13: "record voice note should be just an MCP tool and an item in
 *  the hints"). */
const VN = "Start a call with Vexa";
/** …and right after it, the shared-workspace starter (founder 2026-10-14). */
const SW = "Create a shared workspace";
const item = (id: string, act: string, label = "Pilot sync"): DeskProposal =>
  ({ id, source: `meeting:${id}`, source_label: label, act, since: "2026-10-01T09:00:00Z", status: "open", by: "post-meeting" });

describe("nothing connected", () => {
  it("offers to connect email, then calendar, then a call with Vexa and the shared-workspace starter — and nothing else", () => {
    const ps = proposals(NONE, null, NOW, FINISHED);
    expect(labels(ps)).toEqual(["Connect your email", "Connect your calendar", VN, SW]);
    expect(ps.map((p) => p.provider)).toEqual(["google_email", "google_calendar", undefined, undefined]);
    // opening Connections says nothing to the agent; only the shared-workspace starter is a turn
    expect(ps.filter((p) => p.kick).map((p) => p.kind)).toEqual(["shared-workspace"]);
  });

  it("a connection that exists but is not ready is offered as Connect, opening on THAT connection", () => {
    const stuck = row("google_email", "awaiting_user", "a".repeat(32));
    const broken = row("google_calendar", "disconnected", "b".repeat(32));
    const ps = proposals([stuck, broken], null, NOW);
    expect(labels(ps)).toEqual(["Connect your email", "Connect your calendar", VN, SW]);
    expect(ps.map((p) => p.connectionId)).toEqual(["a".repeat(32), "b".repeat(32), undefined, undefined]);
  });
});

describe("email only", () => {
  it("offers Check my email and Connect your calendar", () => {
    expect(labels(proposals(EMAIL_ONLY, null, NOW))).toEqual(["Check my email", "Connect your calendar", VN, SW]);
  });

  it("Check my email is the person's own words, and the kick names the mail tools and the read-first rule", () => {
    const [p] = proposals(EMAIL_ONLY, null, NOW);
    expect(p).toBe(CHECK_EMAIL);
    expect(p.say).toBe("Check my email.");
    expect(p.kick).toMatch(/gmail_inbox/);
    expect(p.kick).toMatch(/need a reply/);
    expect(p.kick).toMatch(/Do not draft or send/);
  });

  it("events are never consulted without a ready calendar — a stray list offers no prep", () => {
    expect(kinds(proposals(EMAIL_ONLY, [PRICING], NOW))).not.toContain("prep");
  });
});

describe("calendar only", () => {
  it("with a meeting coming up: Prepare for it, then Connect your email", () => {
    const ps = proposals(CAL_ONLY, [PRICING], NOW);
    expect(labels(ps)).toEqual([`Prepare for Acme — pricing review ${atTime(NOW + 75 * 60000, NOW)}`, "Connect your email", VN, SW]);
    expect(ps[0].label).toMatch(/^Prepare for Acme — pricing review at /);
  });

  it("with nothing coming up: Connect your email, and the call with Vexa", () => {
    expect(labels(proposals(CAL_ONLY, [], NOW))).toEqual(["Connect your email", VN, SW]);
  });
});

describe("both connected", () => {
  it("with a meeting coming up: Prepare for it, then Check my email", () => {
    const ps = proposals(BOTH, [PRICING], NOW);
    expect(kinds(ps)).toEqual(["prep", "check-email", "call", "shared-workspace"]);
    expect(ps[0].say).toBe(`${ps[0].label}.`);
    expect(ps[0].kick).toContain('"Acme — pricing review"');
    expect(ps[0].kick).toContain(new Date(NOW + 75 * 60000).toISOString());
    expect(ps[0].kick).toMatch(/calendar_events/);
    expect(ps[0].kick).toMatch(/transcripts/);
    expect(ps[0].kick).toMatch(/not instructions/);
  });

  it("with no meeting coming up: Check my email, and the call with Vexa", () => {
    expect(labels(proposals(BOTH, [], NOW))).toEqual(["Check my email", VN, SW]);
  });

  it("while the calendar read has not answered, or failed, the rest of the row stands", () => {
    expect(labels(proposals(BOTH, null, NOW))).toEqual(["Check my email", VN, SW]);
    expect(labels(proposals(BOTH, "error", NOW))).toEqual(["Check my email", VN, SW]);
  });

  it("a meeting tomorrow morning says tomorrow", () => {
    const ps = proposals(BOTH, [ev("Board", 21 * 60)], NOW);
    expect(ps[0].label).toMatch(/^Prepare for Board tomorrow at /);
  });
});

describe("connections status unreadable", () => {
  it("shows NO chips at all — not even the ones that do not depend on it", () => {
    expect(proposals("error", [PRICING], NOW, BLANK, "a@b.c", [item("r1", "Send the deck")])).toEqual([]);
  });
});

describe("connections status not answered yet", () => {
  it("offers no chip at all — not even the call with Vexa, so the row never reorders as it answers", () => {
    expect(proposals(null, null, NOW)).toEqual([]);
  });
});

describe("the next meeting — the window, and never a placeholder name", () => {
  it("is 24 hours", () => expect(UPCOMING_WINDOW_MS).toBe(24 * 60 * 60 * 1000));

  it("the SOONEST titled event wins", () => {
    expect(nextMeeting([ev("Later", 300), ev("Sooner", 20)], NOW)?.title).toBe("Sooner");
  });

  it("an untitled event is skipped, never named — the next titled one is offered", () => {
    const ps = proposals(BOTH, [ev("", 10), ev("   ", 15), ev("Design sync", 60)], NOW);
    expect(ps[0].label).toMatch(/^Prepare for Design sync /);
  });

  it("no titled event in the window → no chip", () => {
    expect(kinds(proposals(BOTH, [ev("", 10)], NOW))).not.toContain("prep");
  });

  it("an event outside the window, already started, or all-day → no chip", () => {
    for (const e of [ev("Next week", 25 * 60), ev("Started", -5), ev("Holiday", null)])
      expect(kinds(proposals(BOTH, [e], NOW))).not.toContain("prep");
  });

  it("no chip in any state ever says 'Google Meet call', 'Recap', 'to review' or 'Send Vexa'", () => {
    const states: [ConnectionRow[], UpcomingEvent[]][] = [[NONE, []], [EMAIL_ONLY, []], [CAL_ONLY, [PRICING]], [BOTH, [PRICING]], [BOTH, []]];
    for (const [c, e] of states)
      for (const l of labels(proposals(c, e, NOW)))
        expect(l).not.toMatch(/Google Meet call|Recap|to review|Send Vexa/);
  });

  it("a title is one line and bounded", () => {
    const m = nextMeeting([ev(`Line one\n\nline two ${"x".repeat(300)}`, 30)], NOW)!;
    expect(m.title).not.toContain("\n");
    expect(m.title.length).toBeLessThanOrEqual(120);
  });
});

describe("the rows other cars own stay, after the connection chips", () => {
  it("agent-filed jobs and the empty-desk setup follow, and the row is cut at five", () => {
    const ps = proposals(BOTH, [PRICING], NOW, BLANK, "a@b.c", [item("r1", "Send the deck"), item("r2", "Book the room")]);
    expect(kinds(ps)).toEqual(["prep", "check-email", "call", "shared-workspace", "jtbd"]);
    expect(ps).toHaveLength(PROPOSALS_MAX);
    expect(kinds(proposals(NONE, null, NOW, BLANK, "a@b.c"))).toEqual(["connect", "connect", "call", "shared-workspace", "setup"]);
  });

  it("a job says its source", () => {
    expect(jtbdProposal(item("r1", "Send the deck")).source).toBe("Pilot sync");
  });

  it("setup is offered only to a desk nothing was ever written in", () => {
    expect(needsSetup(null)).toBe(false);
    expect(needsSetup(FINISHED)).toBe(false);
    expect(needsSetup(BLANK)).toBe(true);
    expect(setupProposal("a@b.c").kick).toBe(ONBOARDING_GROUNDING + ONBOARDING_REPLY_SEP + "My email is a@b.c, set up a workspace for me");
  });
});

describe("what a click does", () => {
  const chat = (over: Partial<Chat> = {}): Chat =>
    ({ id: "c1", label: "New chat", workspaces: ["personal", "_global"], artifacts: [], createdAt: NOW, lastActivityAt: NOW, ...over });

  it("a connect chip opens Connections on that provider and touches no chat", () => {
    const [email, cal] = proposals([row("google_email", "disconnected", "d".repeat(32))], null, NOW);
    expect(applyProposal(email, chat(), NOW)).toEqual({ act: "connect", provider: "google_email", connectionId: "d".repeat(32) });
    expect(applyProposal(cal, chat(), NOW)).toEqual({ act: "connect", provider: "google_calendar", connectionId: undefined });
  });

  it("Check my email runs IN the chat in front, naming it if nobody did", () => {
    const eff = applyProposal(CHECK_EMAIL, chat(), NOW);
    expect(eff.act).toBe("run");
    if (eff.act !== "run") return;
    expect(eff.chat.id).toBe("c1");
    expect(eff.chat.touched).toBe(true);
    expect(isUnlabeled("New chat")).toBe(true);
    expect(eff.chat.label).toBe("Email check");
    expect(eff.say).toBe("Check my email.");
  });

  it("a chat somebody named keeps its name", () => {
    const eff = applyProposal(CHECK_EMAIL, chat({ label: "Inbox triage" }), NOW);
    expect(eff.act === "run" && eff.chat.label).toBe("Inbox triage");
  });

  it("Start a call with Vexa opens a call in a chat of its own: no turn, the chat in front untouched", () => {
    expect(CALL_VEXA.label).toBe(VN);
    expect(CALL_VEXA.kick).toBeUndefined();
    expect(applyProposal(CALL_VEXA, chat(), NOW)).toEqual({ act: "call" });
    expect(applyProposal(CALL_VEXA, null, NOW)).toEqual({ act: "call" });
  });

  it("Create a shared workspace is a turn: the agent asks the purpose, picks a template, creates, links, offers the event", () => {
    expect(SHARED_WORKSPACE.label).toBe("Create a shared workspace");
    expect(SHARED_WORKSPACE.say).toBe("Create a shared workspace.");
    for (const tool of ["workspace_templates_list", "workspace_create", "workspace_invite_link_create", "workspace_invite", "calendar_event_create"])
      expect(SHARED_WORKSPACE.kick).toContain(tool);
    const eff = applyProposal(SHARED_WORKSPACE, null, NOW);
    expect(eff).toMatchObject({ act: "create", label: "Shared workspace", say: "Create a shared workspace." });
  });

  it("with no chat in front, one is created", () => {
    const [prep] = proposals(BOTH, [PRICING], NOW);
    expect(applyProposal(prep, null, NOW)).toMatchObject({ act: "create", label: "Prep: Acme — pricing review", say: `${prep.label}.` });
  });
});
