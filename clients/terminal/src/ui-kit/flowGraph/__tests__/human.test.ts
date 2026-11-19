/** A FLOW IN HUMAN TERMS (founder, 2026-10-14, on a live call: "flows do not make sense to me
 *  either"). The routine strip says one line — what it is, when, the next run as a DATE, and how the
 *  last run went — and its expanded view says When → Does → Results go to, what it may do as a
 *  question, and a refusal as a sentence with its fix. No engine words, no cron strings, never
 *  "in 8759 h". */
import { describe, it, expect } from "vitest";
import { doesWords, nextWords, permissionRows, refusalWords, resultsWords, routineLine, statusWord, whenWords } from "../human";
import type { FlowDef } from "../model";

// Wed 14 Oct 2026, 16:04 in the viewer's zone — every expectation below is built on the viewer's
// clock, so these hold in any TZ the suite runs in.
const NOW = new Date(2026, 9, 14, 16, 4, 0);
const at = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const utcCron = (local: Date, dow = "*") => `${local.getUTCMinutes()} ${local.getUTCHours()} * * ${dow}`;
const atCron = (d: Date) => `${d.getUTCMinutes()} ${d.getUTCHours()} ${d.getUTCDate()} ${d.getUTCMonth() + 1} *`;
const ENGINE = /agent\.run|background policy|\bbell\b|cron|human_session_required|request_meeting_bot|\d+ \d+ \d+ \d+ \*|\d{3,} ?h\b/;

const digest: FlowDef = { name: "nightly-chat-digest", steps: ["agent_run"], enabled: true, status: "active",
  trigger: { kind: "schedule", cron: utcCron(new Date(2026, 9, 14, 23, 0)) },
  instruction: "Summarise today's chats into my digest page.", last_run: { status: "done", started_at: NOW.getTime() / 1000 - 3600 } };

// The founder's case: "bot at 16:00" became a yearly cron whose next run was "in 8759 h", and its
// bot was refused because nobody was there to approve it.
const oneShot: FlowDef = { name: "bot-at-1600", steps: ["agent_run"], enabled: true, status: "active",
  trigger: { kind: "at", cron: atCron(new Date(2026, 9, 14, 15, 59)) },
  lifecycle: "once", instruction: "Send a bot to the 16:00 partner call and take notes.",
  policy: { key: "routine_background", allowed: ["read"], refused: ["request_meeting_bot"] },
  last_run: { status: "done", started_at: NOW.getTime() / 1000 - 300 },
  last_refusal: { reason: "human_session_required", text: "Couldn't send the bot: request_meeting_bot needs a person present (human_session_required)." },
  grants: [] };

describe("when", () => {
  it("says a schedule in words on the viewer's clock", () => {
    expect(whenWords(digest)).toBe("every day 23:00");
    expect(whenWords({ ...digest, trigger: { kind: "schedule", cron: utcCron(new Date(2026, 9, 14, 23, 0), "1-5") } }))
      .toMatch(/^every weekday 23:00$|^every weekday \d\d:\d\d UTC$/);
    expect(whenWords({ ...digest, trigger: { kind: "schedule", cron: "*/15 * * * *" } })).toBe("every 15 min");
    expect(whenWords({ ...digest, trigger: { kind: "schedule", cron: "5 * * * *" } })).toBe("every hour at :05");
  });
  it("says a one-time routine as once, with its date — never a yearly schedule", () => {
    const w = whenWords(oneShot, NOW);
    expect(w).toMatch(/^once, /);
    expect(w).not.toMatch(ENGINE);
  });
  it("says an event as what happens", () => {
    expect(whenWords({ ...digest, trigger: { kind: "event", event: "meeting.completed" } })).toBe("when a meeting ends");
  });
  it("never shows a cron it cannot read", () => {
    expect(whenWords({ ...digest, trigger: { kind: "schedule", cron: "0 0 L * ?" } })).toBe("on a schedule");
  });
});

describe("next run", () => {
  it("is a date, never a count of hours", () => {
    expect(nextWords(digest, NOW)).toBe(`today ${at(new Date(2026, 9, 14, 23, 0))}`);
    const tomorrow = new Date(2026, 9, 15, 9, 0);
    expect(nextWords({ ...digest, trigger: { kind: "schedule", cron: utcCron(tomorrow) } }, NOW)).toBe(`tomorrow ${at(tomorrow)}`);
    const later = new Date(2026, 9, 20, 9, 0);
    const n = nextWords({ ...digest, trigger: { kind: "schedule", cron: `${later.getUTCMinutes()} ${later.getUTCHours()} ${later.getUTCDate()} ${later.getUTCMonth() + 1} *` } }, NOW)!;
    expect(n).toContain(at(later));
    expect(n).toContain(later.toLocaleDateString([], { weekday: "short" }));
    expect(n).not.toMatch(/\bin \d+ h/);
  });
  it("is minutes away only when it is under the hour", () => {
    const soon = new Date(NOW.getTime() + 12 * 60_000);
    expect(nextWords({ ...digest, trigger: { kind: "schedule", cron: utcCron(soon) } }, NOW)).toBe(`today ${at(soon)} (in 12 min)`);
  });
  it("a one-time routine that already ran has no next run", () => {
    expect(nextWords(oneShot, NOW)).toBeNull();
  });
  it("a paused routine has no next run", () => {
    expect(nextWords({ ...digest, enabled: false }, NOW)).toBeNull();
  });
});

describe("the one line", () => {
  it("routine · when · next · how the last run went", () => {
    expect(routineLine(digest, NOW)).toBe(`Routine · every day 23:00 · next today ${at(new Date(2026, 9, 14, 23, 0))} · ok`);
  });
  it("says a refused run plainly, and a one-shot's line has no next", () => {
    const line = routineLine(oneShot, NOW);
    expect(line).toMatch(/^Routine · once, .* · needs permission$/);
    expect(line).not.toMatch(ENGINE);
  });
  it("paused and retired", () => {
    expect(statusWord({ ...digest, enabled: false })).toBe("paused");
    expect(routineLine(digest, NOW, "retired")).toBe("Routine · every day 23:00 · retired");
    expect(statusWord({ ...digest, last_run: null })).toBe("not run yet");
    expect(statusWord({ ...digest, last_run: { status: "failed", started_at: 1 } })).toBe("failed");
  });
});

describe("the flow, in words", () => {
  it("does: the instruction, in the person's own words", () => {
    expect(doesWords(digest)).toBe("Summarise today's chats into my digest page.");
    expect(doesWords({ ...digest, instruction: undefined, steps: ["request_meeting_bot"] })).toBe("sends a bot to the meeting");
    expect(doesWords({ ...digest, instruction: undefined, steps: ["agent_run"] })).toBe("asks the agent to do its task");
  });
  it("results go to this chat, and to a connection when it notifies one", () => {
    expect(resultsWords(digest)).toBe("this chat");
    expect(resultsWords({ ...digest, notify: "Slack" })).toBe("this chat and Slack");
  });
  it("permissions are questions with yes or no; the one this routine needs and lacks is offered", () => {
    const rows = permissionRows(oneShot);
    const bots = rows.find((r) => r.grant === "bot.request")!;
    expect(bots).toMatchObject({ label: "May send bots", granted: false });
    expect(bots.ask).toMatch(/bot-at-1600/);
    expect(bots.ask).toMatch(/send bots to meetings/);
    // a one-time routine retires by itself after it fires; nobody is asked about that
    expect(rows.find((r) => r.grant === "flow.retire_self")).toBeUndefined();
    expect(permissionRows({ ...oneShot, grants: ["flow.retire_self"] }).find((r) => r.grant === "flow.retire_self"))
      .toMatchObject({ label: "May retire itself", granted: true });
    // a routine that has nothing to do with bots is not asked about bots
    expect(permissionRows(digest).find((r) => r.grant === "bot.request")).toBeUndefined();
    expect(permissionRows({ ...oneShot, grants: ["bot.request"] }).find((r) => r.grant === "bot.request")!.granted).toBe(true);
  });
  it("a refusal is a sentence with its fix, not the engine's reason", () => {
    const r = refusalWords(oneShot)!;
    expect(r.sentence).toBe("It couldn't send the bot: nobody was here to approve it.");
    expect(r.fix).toBe("bot.request");
    expect(r.sentence).not.toMatch(ENGINE);
    expect(refusalWords(digest)).toBeNull();
    // already granted since: the refusal is history, no fix offered
    expect(refusalWords({ ...oneShot, grants: ["bot.request"] })?.fix).toBeUndefined();
    const limit = refusalWords({ ...digest, last_refusal: { reason: "quota", text: "Daily run limit reached.", next_run_at: NOW.getTime() / 1000 + 3600 } })!;
    expect(limit.sentence).toMatch(/^It reached today's run limit; it can run again at \d\d:\d\d\.$/);
  });
});
