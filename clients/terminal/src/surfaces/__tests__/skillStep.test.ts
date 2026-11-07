/** A skill the agent loads is a STEP of its turn, never the person's words.
 *
 *  The harness writes a loaded skill's body into the transcript as a user-role line; agent-api's
 *  history reader now turns it into the op `{label: "skill", skill}` on the agent's turn. These pin
 *  that the terminal names that step — from history and live — instead of a bare "Working".
 */
import { describe, expect, it } from "vitest";
import { historyOp, toolOp } from "../chat";

describe("loaded skill renders as a step", () => {
  it("history op names the skill", () => {
    expect(historyOp({ label: "skill", skill: "scheduling" }).label).toBe("Loaded skill · scheduling");
  });
  it("history op without a name still reads as a skill step", () => {
    expect(historyOp({ label: "skill" }).label).toBe("Loaded skill");
  });
  it("live Skill tool call names the skill", () => {
    expect(toolOp("Skill", { skill: "scheduling" }).label).toBe("Loading skill · scheduling");
  });
});
