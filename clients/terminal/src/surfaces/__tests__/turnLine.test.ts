/** Founder screenshots 2026-10-16: a finished turn read "Working · 1 step", and a "README.md" chip sat
 *  under an answer that was not about it. A done step reads in the past tense; a file chip shows only
 *  when the answer is about that file. */
import { describe, expect, it } from "vitest";
import { chipFiles, doneLabel, shownAttachments } from "../agent-window";

describe("doneLabel", () => {
  it("puts a finished step in the past tense", () => {
    expect(doneLabel("Working")).toBe("Done");
    expect(doneLabel("Reading · plan.md")).toBe("Read · plan.md");
    expect(doneLabel("Searching · notes")).toBe("Searched · notes");
    expect(doneLabel("Writing · plan.md")).toBe("Wrote · plan.md");
    expect(doneLabel("Running · bash")).toBe("Ran · bash");
    expect(doneLabel("Browsing · web_fetch")).toBe("Browsed · web_fetch");
    expect(doneLabel("Loading skill · x")).toBe("Loaded skill · x");
    expect(doneLabel("workspace_invite")).toBe("workspace_invite");
  });
});

describe("chipFiles", () => {
  const ops = [{ file: "/ws/a/README.md", wrote: true }, { file: "/ws/a/plan.md", wrote: true }, { file: "/ws/a/x.md" }] as never[];
  it("shows only files the answer is about", () => {
    expect(chipFiles(ops, "I updated the plan: plan.md now lists the dates.")).toEqual(["/ws/a/plan.md"]);
    expect(chipFiles(ops, "Who should I invite next?")).toEqual([]);
    expect(chipFiles(ops, "I rewrote the README with the new goals.")).toEqual(["/ws/a/README.md"]);
  });
});

describe("shownAttachments", () => {
  const a = [{ path: "p.png", name: "p.png" }] as never[];
  it("draws a message's files only under a message the person can see", () => {
    expect(shownAttachments({ id: "h-u-3", text: "", attachments: a })).toEqual([]);
    expect(shownAttachments({ id: "h-u-3", text: "look", attachments: a })).toEqual(a);
    expect(shownAttachments({ id: "u-7", text: "", attachments: a })).toEqual(a);
  });
});
