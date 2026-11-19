/** A turn the meeting's signals woke arrives as the AGENT's message in the meeting's chat, with a
 *  small "From the meeting" marker — never the composed wake prompt as the person's bubble, and never
 *  a raw trigger name. */
import { describe, expect, it } from "vitest";
import { foldSignalWakes, isSignalWake, FROM_MEETING } from "../signalWake";

const wakePrompt = "[vexa-flow:meeting-signal:wake] Signals since your last look (score 1.3 against your brief): …";

describe("isSignalWake", () => {
  it("recognises the flow mark of a signal-started turn, and an explicit trigger", () => {
    expect(isSignalWake({ role: "user", raw: wakePrompt })).toBe(true);
    expect(isSignalWake({ role: "user", raw: "[vexa-flow:transcript-signals:on_meeting_signal] x" })).toBe(true);
    expect(isSignalWake({ role: "user", raw: "anything", trigger: "signal" })).toBe(true);
  });
  it("leaves every other turn alone", () => {
    expect(isSignalWake({ role: "user", raw: "[vexa-flow:post_meeting:process_meeting] x" })).toBe(false);
    expect(isSignalWake({ role: "user", raw: "what was the signal on that?" })).toBe(false);
    expect(isSignalWake({ role: "agent", raw: wakePrompt })).toBe(false);
  });
});

describe("foldSignalWakes", () => {
  it("drops the wake prompt and marks the reply it produced", () => {
    const turns = [
      { role: "user" as const, text: "hi", raw: "hi" },
      { role: "agent" as const, text: "hello", raw: "hello" },
      { role: "user" as const, text: "Signal", raw: wakePrompt },
      { role: "agent" as const, text: "Noted the Friday decision.", raw: "Noted the Friday decision." },
      { role: "user" as const, text: "thanks", raw: "thanks" },
      { role: "agent" as const, text: "any time", raw: "any time" },
    ];
    const out = foldSignalWakes(turns);
    expect(out.map((t) => t.text)).toEqual(["hi", "hello", "Noted the Friday decision.", "thanks", "any time"]);
    expect(out.map((t) => !!t.fromMeeting)).toEqual([false, false, true, false, false]);
  });
  it("marks the reply whichever name the record gives the agent's role", () => {
    const out = foldSignalWakes([{ role: "user", raw: wakePrompt }, { role: "assistant", text: "Noted." }]);
    expect(out).toEqual([{ role: "assistant", text: "Noted.", fromMeeting: true, wokeBy: "call" }]);
  });
  it("the marker words are plain", () => {
    expect(FROM_MEETING).toBe("From the meeting");
  });
});
