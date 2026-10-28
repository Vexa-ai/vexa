/** Flows phase 0: a routine says on its strip what it may do with nobody there — the grant its
 *  person gave it when they made it in chat ("This routine may: send bots to meetings"). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { RoutineBar } from "../RoutineBar";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("routine strip grant line", () => {
  it("shows the may sentence from the routine's card, and nothing when it may do nothing alone", async () => {
    const card = { name: "bot-at-1213", trigger: { kind: "at", cron: "13 12 1 3 *" },
                   steps: ["request_meeting_bot"], enabled: true, status: "active", grants: ["bot.request"],
                   may: "This routine may: send bots to meetings." };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ routines: [card] }))));
    const { container } = render(<RoutineBar routine={{ flow: "bot-at-1213", scope: "user:1", version: 1 }} />);
    // the person reads it as a permission row; the engine's sentence is the operator's, under Details
    await waitFor(() => expect(container.querySelector("[data-routine-line]")?.textContent).toMatch(/^Routine · once/));
    fireEvent.click(container.querySelector("[data-routine-expand]")!);
    expect(container.querySelector('[data-routine-perm="bot.request"]')?.textContent).toBe("May send bots: yes");
    fireEvent.click(container.querySelector("[data-routine-details]")!);
    await waitFor(() => expect(container.querySelector("[data-routine-may]")?.textContent)
      .toBe("This routine may: send bots to meetings."));
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ routines: [{ ...card, may: undefined }] }))));
    const second = render(<RoutineBar routine={{ flow: "bot-at-1213", scope: "user:1", version: 1 }} />);
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.click(second.container.querySelector("[data-routine-expand]")!);
    fireEvent.click(second.container.querySelector("[data-routine-details]")!);
    expect(second.container.querySelector("[data-routine-raw]")).not.toBeNull();
    expect(second.container.querySelector("[data-routine-may]")).toBeNull();
  });
});

import { sensorLine, firesLine } from "../RoutineBar";

describe("routine strip sensor and keyed lines (ADR-0050)", () => {
  it("says the sensor's health and the next armed fires", () => {
    const def = { name: "bot-to-my-meetings",
      sensor: { name: "video-meetings", kind: "calendar", status: "degraded", reason: "rate limited",
                last_tick_at: 1803902400, next_check_at: 1803902460, last_events: [{ type: "calendar.event.updated", title: "Sync", at: 1 }] },
      next_fires: [{ key: "a", fire_at: 1803902340, title: "Sync" }] };
    expect(sensorLine(def)).toMatch(/^Sensor video-meetings: degraded — rate limited · checked .* · next check .* · last: Sync$/);
    expect(firesLine(def)).toMatch(/^Next: \d\d:\d\d Sync$/);
    expect(firesLine({ name: "x", next_fires: [] })).toBe("Nothing armed yet");
    expect(sensorLine({ name: "x" })).toBeNull();
  });
});
