import { describe, it, expect } from "vitest";
import { displayLabel } from "../chats";

// NEVER AN INTERNAL ID ON THE RAIL (#1783; founder screenshot: `scaffold-WMSeRx…`, `pchat-mv747eaw`,
// `vexa_call · call-302959d51…`).
const at = new Date("2026-10-13T22:16:00").getTime();

describe("a row nobody named — no id, no time (founder 2026-10-15: \"remove time\")", () => {
  it.each([
    ["pchat-mv747eaw", null, "New chat"],
    ["scaffold-WMSeRxIAUGg9xyz", null, "New chat"],
    ["", null, "New chat"],
    ["Chat", null, "New chat"],
    ["vexa_call · call-302959d51ab", "7", "Call with Vexa"],
    ["voice_note · vn-1a2b3c4d5e6f", "8", "Voice note"],
    ["Voice note", "8", "Voice note"],
    ["Untitled meeting", "9", "Meeting"],
    ["Google Meet · abc-defg-hij", "3", "Meeting"],
  ])("%s → %s", (label, meetingId, want) => {
    expect(displayLabel({ label, when: at, meetingId })).toBe(want);
  });
  it("a real name is left alone and never carries a time", () => {
    expect(displayLabel({ label: "Acme renewal", when: at })).toBe("Acme renewal");
  });
});
