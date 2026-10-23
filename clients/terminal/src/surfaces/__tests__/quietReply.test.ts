/** A SIGNAL TURN MAY END SILENTLY (friction 6): the agent answers a wake it has nothing to say to with
 *  `[vexa-quiet]` (core/agent/shared/marks.py QUIET_REPLY). The live stream must never paint it: a
 *  reply that is (or opens with) the marker shows nothing at all, and a reply that did page work
 *  shows its words without the marker — even when the marker arrives split across deltas. */
import { describe, it, expect } from "vitest";
import { QUIET_REPLY, quietDeltas } from "../chatStream";

function run(deltas: string[]): string {
  const q = quietDeltas();
  return deltas.map((d) => q.push(d)).join("") + q.flush();
}

describe("quietDeltas", () => {
  it("shows nothing for a reply that is exactly the marker, whole or split", () => {
    expect(QUIET_REPLY).toBe("[vexa-quiet]");
    expect(run(["[vexa-quiet]"])).toBe("");
    expect(run(["[vexa", "-qui", "et]"])).toBe("");
    expect(run(["\n  [VEXA-QUIET]  "])).toBe("");
  });

  it("shows nothing for a reply that opens with the marker", () => {
    expect(run(["[vexa-quiet]", " nothing new here"])).toBe("");
  });

  it("strips the marker from a reply that did page work", () => {
    expect(run(["Updated the page.", "\n[vexa-", "quiet]"])).toBe("Updated the page.\n");
  });

  it("leaves an ordinary reply untouched, including a bracket that is not the marker", () => {
    expect(run(["Hello ", "there."])).toBe("Hello there.");
    expect(run(["[vexa", " is great]"])).toBe("[vexa is great]");
    expect(run(["see [v"])).toBe("see [v");
  });
});
