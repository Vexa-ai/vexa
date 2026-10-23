import { describe, expect, it } from "vitest";
import { roomTurnRow } from "../../surfaces/chat";

// Founder, app.dev 2026-10-16: the room shows AT ONCE that Vexa is on it, at the BOTTOM of the room
// above the composer (where a typing indicator lives) — "Vexa heard you · thinking…" from the moment
// a wake is accepted, the step line under it once the turn starts, replaced by the reply, or "Vexa:
// nothing to add" for 3 s. The header line stays as it was.
describe("the room's live status above the composer", () => {
  it("says Vexa heard, the moment the server says thinking", () => {
    expect(roomTurnRow("thinking", null, false)).toEqual({ line: "Vexa heard you \u00b7 thinking\u2026", step: null });
  });
  it("adds the step the turn is on", () => {
    const r = roomTurnRow("thinking", { tool: "Write", file_path: "kg/entities/meeting/m.md" }, false);
    expect(r?.step).toMatch(/^Writing/);
    expect(r?.step).toContain("m.md");
  });
  it("says nothing to add only while that is being shown, and is quiet otherwise", () => {
    expect(roomTurnRow("nothing", null, true)).toEqual({ line: "Vexa: nothing to add", step: null });
    expect(roomTurnRow("nothing", null, false)).toBeNull();
    expect(roomTurnRow(null, null, false)).toBeNull();
  });
});
