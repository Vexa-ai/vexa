/** JTBD-1 noise 4, 5, 7: a workspace page shows the person's own pages and human events, not the
 *  scaffolding and commit plumbing; the home offers one way to make a workspace. */
import { describe, expect, it } from "vitest";
import { homeChips, humanActivity, isScaffolding, peoplePages } from "../desktop/home2/workspaceNoise";

describe("workspace noise (home2)", () => {
  it("hides scaffolding files and keeps README and the person's pages", () => {
    const tree = ["README.md", "CLAUDE.md", "AGENTS.md", "flows/standup.md", "skills/x/SKILL.md", "policies/a.md",
      "templates/t.md", "_system/x.md", "launch/plan.md", "notes.md"];
    expect(tree.filter(isScaffolding)).toEqual(["CLAUDE.md", "AGENTS.md", "flows/standup.md", "skills/x/SKILL.md", "policies/a.md", "templates/t.md", "_system/x.md"]);
    expect(peoplePages(tree)).toEqual(["README.md", "launch/plan.md", "notes.md"]);
  });
  it("keeps only human-meaningful activity, in human words", () => {
    const c = (msg: string, kind = "you") => ({ sha: msg, msg, kind } as never);
    const out = humanActivity([c("policy: owner 31 for launch-plan-13c76e"), c("seed"), c("workspace identity"),
      c("edit README.md (terminal page editor)"), c("Added the agenda", "member"), c("sync", "system")]);
    expect(out.map((x: { msg: string }) => x.msg)).toEqual(["Edited README", "Added the agenda"]);
  });
  it("drops the duplicate Create-a-shared-workspace chip only with home2", () => {
    const chips = [{ kind: "call" }, { kind: "shared-workspace" }] as never[];
    expect(homeChips(chips, true)).toHaveLength(1);
    expect(homeChips(chips, false)).toHaveLength(2);
  });
});
