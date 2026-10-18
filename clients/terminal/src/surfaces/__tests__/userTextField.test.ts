/** F47 — the person's words are their own field, and stripping is only the fallback.
 *
 *  THE REGRESSION THESE PIN. Chat history is read out of the harness transcript, which stores the
 *  prompt the model was GIVEN: the worker's voice/kg-links/mount-stack/entity-index/global-context
 *  preambles, then the control plane's grounding, then the sentence somebody typed. The terminal
 *  reconstructed the human half by stripping all of that off the front. On 2026-09-02 the preamble
 *  set changed shape, no sentinel had been written for turns with no server grounding, the fallback
 *  regexes no longer matched — and the founder's entire machinery prompt rendered as a grey USER
 *  bubble, his own sentence buried at the end of it.
 *
 *  So: a turn that carries `user_text` renders that and only that, and the two strip paths stay
 *  underneath it for records written before the field existed.
 */
import { describe, expect, it } from "vitest";
import { historyUserText } from "../chat";

const PREAMBLES =
  "## Referencing knowledge (always)\n\nrules — create the entity first, or use plain text.\n\n" +
  "## Your mounted workspaces\n\nThis turn mounts a STACK of workspaces (the three-tier mount stack).\n" +
  "- `/workspaces/127` — **seed** (your DESK — your private baseline, durable personal memory — read-write)\n" +
  "Write-routing policy:\n- never write a read-only mount\n" +
  "Always use ABSOLUTE paths under the mount you intend — do not guess or invent mount paths.\n";

describe("historyUserText — the field wins, always", () => {
  it("renders ONLY the person's words when the turn carries them", () => {
    const t = { text: PREAMBLES + "what did we decide about the ASWF licence?", user_text: "what did we decide about the ASWF licence?" };
    expect(historyUserText(t)).toBe("what did we decide about the ASWF licence?");
  });

  it("does not care what the composed prompt looks like — a preamble nothing recognises is still not shown", () => {
    // the exact failure mode of 2026-09-02: a preamble shape no regex covers, and no sentinel
    const t = { text: "## Some preamble invented next month\n\nnovel wording\nprepare me for the TSC call", user_text: "prepare me for the TSC call" };
    expect(historyUserText(t)).toBe("prepare me for the TSC call");
  });

  it("an empty message stays empty rather than falling back to the machinery", () => {
    // `??` and not `||`: a recorded empty string is a recorded fact, and falling through to the
    // strip on it would put the composed prompt back on screen — the very bug.
    expect(historyUserText({ text: PREAMBLES + "x", user_text: "" })).toBe("");
  });
});

describe("historyUserText — the fallbacks, for records written before the field", () => {
  it("an old record with no user_text but a sentinel still strips at the sentinel", () => {
    const raw = PREAMBLES + "<!--vexa:user-input-below-->Interview me to build the brief.";
    expect(historyUserText({ text: raw })).toBe("Interview me to build the brief.");
  });

  it("an old record with neither still strips by matching the preamble blocks", () => {
    const raw =
      "## Referencing knowledge (always)\n\nblah rules — create the entity first, or use plain text.\n\n" +
      "## Your mounted workspaces\n\ntier list...\nAlways use ABSOLUTE paths under the mount you intend — do not guess or invent mount paths.\n" +
      "what did we decide in my last meeting?";
    expect(historyUserText({ text: raw })).toBe("what did we decide in my last meeting?");
  });

  it("a record that is neither is left alone (fail-soft)", () => {
    expect(historyUserText({ text: "plain question" })).toBe("plain question");
  });
});
