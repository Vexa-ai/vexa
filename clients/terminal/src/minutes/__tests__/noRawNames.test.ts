/** NO RAW ID IN ANYTHING A PERSON READS (UI decisions 14; founder 2026-10-15, on a tab reading
 *  "vexa_call · call-21ca0ef92…": "humans get crazy with these names"). Every name a meeting or a
 *  chat can be shown under — the calls list, the rail row, a notification — and this suite fails the
 *  moment one of them renders something that looks like an id, a platform key or a time. */
import { describe, expect, it } from "vitest";
import { callLabel, looksRaw, meetingDisplayName, readableSentence } from "../callsList";
import { displayLabel, meetingTitle } from "../chats";

const ROWS = [
  { id: "41", platform: "vexa_call", native_id: "call-21ca0ef9214d", title: "Call" },
  { id: "42", platform: "google_meet", native_id: "tog-zpha-qim", title: "Google Meet call" },
  { id: "43", platform: "zoom", native_id: "92446951537", title: "zoom · 92446951537" },
  { id: "44", platform: "voice_note", native_id: "vn-1a2b3c4d5e6f", title: "Voice note" },
  { id: "45", platform: "google_meet", native_id: "abc-defg-hij", title: "vexa_call · call-302959d51ab", participants: [{ name: "Valeriia" }] },
] as never[];
const ID = /vexa_call|google_meet|voice_note|call-[0-9a-f]{6,}|[a-z]{3}-[a-z]{4}-[a-z]{3}|\d{9,}|#\d+|\d{1,2}:\d{2}/;

describe("every surface that names a meeting or a chat", () => {
  it("renders no id, no platform key and no time", () => {
    for (const m of ROWS) {
      for (const s of [callLabel(m), meetingTitle(m), displayLabel({ label: (m as { title: string }).title, meetingId: (m as { id: string }).id })]) {
        expect(s, s).not.toMatch(ID);
      }
    }
    for (const label of ["pchat-mv8hj24g", "Chat · 9:11 AM", "scaffold-WMSeRxIAUGg9", ""]) {
      expect(displayLabel({ label }), label).toBe(label === "Chat · 9:11 AM" ? "Chat · 9:11 AM" : "New chat");
    }
  });
  it("a solo call is Call with Vexa; a call with people is named after them", () => {
    expect(meetingDisplayName(ROWS[0])).toBe("Call with Vexa");
    expect(meetingDisplayName(ROWS[4])).toBe("Call with Valeriia");
  });
  it("ids are recognised; names are not", () => {
    for (const raw of ["vexa_call · call-21ca0ef9214d", "tog-zpha-qim", "pchat-mv747eaw", "92446951537"]) expect(looksRaw(raw), raw).toBe(true);
    for (const name of ["AI Tinkerers planning", "Call with Valeriia"]) expect(looksRaw(name), name).toBe(false);
  });
  it("a notification that quotes an id reads with a name instead", () => {
    expect(readableSentence("Vexa joined “call-0c90e2a5fcff”")).toBe("Vexa joined your call");
    expect(readableSentence("Vexa joined “jfw-jmbe-pjx”")).toBe("Vexa joined your meeting");
    expect(readableSentence("Ana shared the chat “Lisbon sponsors” with you")).toBe("Ana shared the chat “Lisbon sponsors” with you");
  });
});
