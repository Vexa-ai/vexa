/** History hygiene: server-side grounding preambles never render as the user's words. */
import { describe, expect, it } from "vitest";
import { stripContextBlocks } from "../chat";

describe("stripContextBlocks", () => {
  it("strips stacked kg-links + mount-stack preambles down to the user's text", () => {
    const raw = "## Referencing knowledge (always)\n\nblah rules — create the entity first, or use plain text.\n\n" +
      "## Your mounted workspaces\n\ntier list...\nAlways use ABSOLUTE paths under the mount you intend — do not guess or invent mount paths.\nwhat did we decide in my last meeting?";
    expect(stripContextBlocks(raw)).toBe("what did we decide in my last meeting?");
  });
  it("strips a live-meeting transcript fold", () => {
    const raw = "You are assisting in a live meeting (google_meet/abc). Its live transcript so far is below — answer.\n\n<transcript>\nJane: hi\n</transcript>\n\nwho spoke?";
    expect(stripContextBlocks(raw)).toBe("who spoke?");
  });
  it("cuts everything up to & including the server boundary sentinel (drift-proof)", () => {
    // grounding whose wording the per-block regexes DON'T recognize — the sentinel still strips it
    const raw = "## Some future preamble we don't have a regex for\n\nwith novel wording\n" +
      "<!--vexa:user-input-below-->Interview me to build the brief.";
    expect(stripContextBlocks(raw)).toBe("Interview me to build the brief.");
  });
  it("strips the schedule digest even with tz/now attributes on the opening tag", () => {
    const raw = '<schedule tz="Europe/Lisbon" now="Thu 2026-07-09 17:50">\nupcoming:\n- x\n</schedule>\n\nwhat is next?';
    expect(stripContextBlocks(raw)).toBe("what is next?");
  });
  it("strips the (grown) prep-meeting steering down to the user's words", () => {
    const raw = 'You are helping the user PREPARE for the meeting "X". say plainly when you don\'t have prior context. ' +
      "Seeded EXAMPLE entities exist only to show how knowledge is kept. " +
      "infer what you can and confirm it instead of starting blank.\n\nInterview me.";
    expect(stripContextBlocks(raw)).toBe("Interview me.");
  });
  it("leaves unrecognized text untouched (fail-soft)", () => {
    expect(stripContextBlocks("plain question")).toBe("plain question");
    const odd = "## Your mounted workspaces\nnever-terminated";
    expect(stripContextBlocks(odd)).toBe(odd);
  });
});
