/** Founder 2026-10-16: a reload while the agent replied dropped the streaming output. With no cursor
 *  of its own, a reloaded chat watches the RUNNING turn from its start (the server's `running_from`),
 *  so the partial reply is drawn and the rest keeps streaming. */
import { describe, expect, it } from "vitest";
import { attachPoint, turnsToWatch } from "../inbox";

describe("a reload mid-turn", () => {
  const view = { pending: [], busy: true, cursor: "9-0", running_from: "5-0" } as never;
  it("watches the running turn", () => {
    expect(turnsToWatch(view, "")).toEqual({ head: "running:5-0" });
  });
  it("attaches from the turn's start, not the tail", () => {
    expect(attachPoint(view, "")).toBe("5-0");
    expect(attachPoint(view, "7-0")).toBe("7-0");             // an exact cursor of its own wins
    expect(attachPoint({ pending: [], cursor: "9-0" } as never, "")).toBe("9-0");
  });
});
