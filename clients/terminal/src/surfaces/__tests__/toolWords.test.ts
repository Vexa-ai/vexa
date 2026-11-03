/** Founder 2026-10-16: the progress line said "Searching · Glob" — an internal tool name. A step says
 *  what the agent is doing in human words, never a harness tool's name. */
import { describe, expect, it } from "vitest";
import { toolOp } from "../chat";

describe("toolOp labels", () => {
  it("names no harness tool", () => {
    expect(toolOp("Glob").label).toBe("Looking through your files");
    expect(toolOp("Grep").label).toBe("Looking through your files");
    expect(toolOp("Bash").label).toBe("Working");
    expect(toolOp("WebFetch").label).toBe("Reading the web");
    expect(toolOp("Read", { file_path: "/w/a/plan.md" }).label).toBe("Reading · Plan");
  });
});
