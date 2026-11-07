/** The routine strip's countdown and the read-only flow graph (founder, 2026-10-13). */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { countdown, cronText, flowGraph, nextRun, scheduleLine, zoneName, type FlowDef } from "../model";
import { FlowGraph } from "../FlowGraph";
import { flowDefOf, ownershipLine, runFacts } from "../../../minutes/RoutineBar";

afterEach(cleanup);
/** The viewer's own wall clock for a UTC instant — what every time on the strip must read as. */
const hhmm = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const NOW = new Date(Date.UTC(2026, 9, 13, 18, 52, 18));
const SWEEP: FlowDef = {
  name: "unanswered-inbox-sweep", scope: "user:2", trigger: { kind: "schedule", cron: "*/15 * * * *" },
  steps: ["agent_run"], instruction: "Support desk sweep.\nCheck every source.", thread: "meet-86",
  last_run: { status: "done", started_at: NOW.getTime() / 1000 - 600, duration_s: 41,
              steps: { agent_run: { state: "confirmed", duration_s: 41 } } },
  runs_today: 12, runs_per_day: 48,
  policy: { key: "connection_actions_unattended", allowed: ["gmail.search", "gmail.read"], refused: ["calendar.events"] },
};

describe("the schedule, read on the viewer's clock", () => {
  it("finds the next slot and says it as a countdown", () => {
    expect(nextRun("*/15 * * * *", NOW)?.toISOString()).toBe("2026-10-13T19:00:00.000Z");
    expect(nextRun("0 9 * * 1-5", NOW)?.toISOString()).toBe("2026-10-14T09:00:00.000Z");
    expect(nextRun("not a cron", NOW)).toBeNull();
    expect(countdown(7 * 60_000 + 42_000)).toBe("7:42");
    expect(countdown(65 * 60_000)).toBe("1h 05m");
    expect(scheduleLine(SWEEP, NOW)).toBe("Next run in 7:42");
    expect(scheduleLine({ ...SWEEP, trigger: { kind: "event", event: "meeting.completed" } }, NOW))
      .toBe(`Fires on meeting.completed · last fired ${hhmm(NOW.getTime() - 600_000)}`);
  });
  it("says it in words", () => {
    expect(cronText("*/15 * * * *")).toBe("every 15 min");
    // the viewer's clock and zone, never UTC beside a local "Next run" (founder, app.dev 2026-10-14)
    const today = (h: number, m: number) => { const d = new Date(); return hhmm(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), h, m)); };
    expect(cronText("0 9 * * *")).toBe(`daily at ${today(9, 0)} ${zoneName()}`);
    expect(cronText("0 9 * * *")).not.toMatch(zoneName() === "UTC" ? /^$/ : /UTC/);
    // a weekday rule whose local time crosses midnight would name the wrong days: it stays in UTC
    const n = new Date(), at = new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate(), 7, 30));
    const crosses = at.getDate() !== at.getUTCDate();
    expect(cronText("30 7 * * 1-5")).toBe(crosses ? "weekdays at 07:30 UTC" : `weekdays at ${today(7, 30)} ${zoneName()}`);
  });
  it("says Paused, or when the daily limit lets the next run start", () => {
    expect(scheduleLine({ ...SWEEP, enabled: false }, NOW)).toBe("Paused");
    const next = NOW.getTime() / 1000 + 3 * 3600;
    expect(scheduleLine({ ...SWEEP, last_refusal: { reason: "quota_runs_per_day", next_run_at: next } }, NOW))
      .toBe(`Limit reached — next possible ${hhmm(next * 1000)}`);
  });
});

describe("the flow graph", () => {
  it("draws trigger → steps → policy gate → outputs, from the definition alone", () => {
    const nodes = flowGraph(SWEEP);
    expect(nodes.map((n) => n.kind)).toEqual(["trigger", "step", "gate", "output", "output"]);
    expect(nodes[0].sub).toBe("every 15 min");
    expect(nodes[1].label).toBe("agent.run");
    expect(nodes[1].status).toBe("ok");
    expect(nodes[1].duration_s).toBe(41);
    expect(nodes[2].detail.lines[0]).toContain("gmail.search");
  });
  it("peeks a node's configuration on click, and draws no red", () => {
    const { container } = render(<FlowGraph flow={SWEEP} />);
    fireEvent.click(container.querySelector('[data-node="step:agent_run"]') as HTMLElement);
    expect(screen.getByRole("region", { name: "Step: agent.run" }).textContent).toContain("Support desk sweep.");
    expect(container.innerHTML).not.toMatch(/--red|#e57373|danger/);
  });
});

describe("the strip's facts", () => {
  it("reads the routine card", () => {
    const def = flowDefOf({ trigger: { kind: "schedule", cron: "*/15 * * * *" }, steps: ["agent_run"],
                            last_run: SWEEP.last_run, runs_today: 12, runs_per_day: 48, enabled: true }, "x");
    expect(runFacts(def)).toEqual([`last run ${hhmm(NOW.getTime() - 600_000)} · ok · 41s`, "12/48 runs today"]);
    expect(runFacts(flowDefOf(undefined, "x"))).toEqual(["not run yet"]);
  });
});

import { RoutineBar } from "../../../minutes/RoutineBar";
import { vi } from "vitest";

describe("the routine strip", () => {
  it("keeps Retire neutral behind a confirmation, and keeps the graph behind Details", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ routines: [] }) }) as unknown as Response));
    const { container } = render(<RoutineBar routine={{ flow: "sweep", scope: "user:2", version: 7 }} />);
    expect(container.innerHTML).not.toMatch(/--red|#e57373|danger/);
    expect(container.querySelector("[data-flow-graph]")).toBeNull();          // collapsed by default
    fireEvent.click(container.querySelector("[data-routine-expand]") as HTMLElement);
    expect(container.querySelector("[data-flow-graph]")).toBeNull();          // the human view first
    fireEvent.click(screen.getByText("Details"));
    expect(container.querySelector("[data-flow-graph]")).not.toBeNull();
    fireEvent.click(screen.getByText("Retire"));
    expect(screen.getByText("Keep")).toBeTruthy();
    vi.unstubAllGlobals();
  });
});

describe("a sink", () => {
  it("says what lands in the chat and when its agent wakes", () => {
    const sink: FlowDef = { name: "acme-mail", kind: "sink", trigger: { kind: "event", event: "mail.received" },
                            steps: ["post_event_card"], wake: "batch:30", filter: { from: "acme" } };
    expect(scheduleLine(sink, NOW)).toBe("Cards from mail.received · agent wakes batch:30");
    expect(flowGraph(sink)[1].label).toBe("event card");
    expect(ownershipLine(sink)).toBe("⇢ sink acme-mail — mail.received where from ~ acme land here as cards · agent wakes batch:30");
  });
});


describe("the founder's sweep: every 30 min, 07:00–22:30 Lisbon", () => {
  const sweep: FlowDef = { name: "unanswered-inbox-sweep", steps: ["agent_run"],
                           trigger: { kind: "schedule", cron: "*/30 6-21 * * *" } };
  it("draws the schedule, never an event, even when only the cron is known", () => {
    expect(flowGraph(sweep)[0].kind).toBe("trigger");
    expect(flowGraph(sweep)[0].label).toBe("schedule");
    expect(flowGraph(flowDefOf({ cron: "*/30 6-21 * * *", steps: ["agent_run"] }, "x"))[0].label).toBe("schedule");
    expect(cronText("*/30 6-21 * * *")).toMatch(/^every 30 min · \d\d:\d\d–\d\d:\d\d \S+/);
  });
  it("outside its window says when and how long, inside it counts down", () => {
    const night = new Date(Date.UTC(2026, 9, 13, 22, 58, 0));
    // the day word is the viewer's own calendar (TZ-dependent); the time left is not
    expect(scheduleLine(sweep, night)).toMatch(/^Next run \d\d:\d\d( tomorrow| \S+)? \(in 7 h 2 min\)$/);
    const next = new Date(Date.UTC(2026, 9, 14, 6, 0, 0));
    const word = next.toDateString() === night.toDateString() ? "" :
      new Date(night.getTime() + 86_400_000).toDateString() === next.toDateString() ? " tomorrow" : null;
    if (word !== null) expect(scheduleLine(sweep, night)).toContain(`${word} (in 7 h 2 min)`);
    const day = new Date(Date.UTC(2026, 9, 14, 9, 52, 18));
    expect(scheduleLine(sweep, day)).toBe("Next run in 7:42");
  });
  it("names the routine once", () => {
    const line = ownershipLine(flowDefOf({ trigger: { kind: "schedule", cron: "*/30 6-21 * * *" } }, "unanswered-inbox-sweep"));
    expect((line + " " + scheduleLine(sweep, NOW)).match(/routine/gi)).toHaveLength(1);
  });
});
