/** Founder screenshot, app.dev 2026-10-18 (OeNB × Vexa pilot): the turn line read "✓ Wrote · 7 steps"
 *  over a reply that said "Nothing was written. The refactored README is held". The privacy hold (D6)
 *  had kept the write for his approval. A held write reads held, a refused or failed one reads not
 *  written — live, after a reload, and when the hold came at the commit (`write-withheld`). */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Conversation, headerOp, opLabel, type Op, type Turn } from "../agent-window";
import { heldApproval, historyOp, markOp, markWithheld, settledStatus, toolOp } from "../chat";

afterEach(cleanup);

const write = (file = "/workspaces/oenb/README.md", status: Op["status"] = "done", callId = "w1"): Op =>
  ({ ...toolOp("mcp__vexa__workspace_write", { file_path: file }), status, callId });
const read = (status: Op["status"] = "done"): Op => ({ ...toolOp("Read", { file_path: "/workspaces/oenb/plan.md" }), status, callId: "r1" });

describe("a step says how it ended", () => {
  it("never says Wrote for a write that was held, refused or failed", () => {
    expect(opLabel(write())).toBe("Wrote · README");
    expect(opLabel(write(undefined, "held"))).toBe("Held for approval · README");
    expect(opLabel(write(undefined, "error"))).toBe("Not written · README");
    expect(opLabel({ label: "notify", status: "held" })).toBe("Held for approval · notify");
    // a failed read keeps its own words (it is not a write)
    expect(opLabel(read("error"))).toBe("Reading · Plan");
  });

  it("the turn's line shows the held write, not the step that ran after it", () => {
    expect(headerOp([read(), write(undefined, "held"), read()]).status).toBe("held");
    expect(headerOp([write(undefined, "error"), read()]).status).toBe("error");
    expect(headerOp([write(), read()]).label).toBe(read().label);
    // while the turn runs, the current step
    expect(headerOp([write(undefined, "held"), read("running")]).status).toBe("running");
  });

  it("renders held with its own tone and no green check", () => {
    const turns: Turn[] = [{ id: "a", role: "agent", text: "Nothing was written. The refactored README is held.",
                             ops: [read(), write(undefined, "held")], steps: 7 }];
    const { container } = render(<Conversation turns={turns} busy={false} />);
    const row = container.querySelector("[data-op-status]");
    expect(row?.getAttribute("data-op-status")).toBe("held");
    expect(container.textContent).toContain("Held for approval · README");
    expect(container.textContent).toContain("7 steps");
    expect(container.textContent).not.toMatch(/Wrote/);
  });
});

describe("results mark their step", () => {
  it("maps the server's outcome to a status", () => {
    expect(settledStatus("held", false)).toBe("held");
    expect(settledStatus("refused", false)).toBe("error");
    expect(settledStatus("failed", false)).toBe("error");
    expect(settledStatus(undefined, false)).toBe("error");
    expect(settledStatus(undefined, true)).toBeNull();
  });

  it("finds the step by its call id, else the newest step of that tool", () => {
    const ops = [write("/workspaces/oenb/a.md", "done", "w0"), write(undefined, "running", "w1"), read("running")];
    expect(markOp(ops, "w1", "mcp__vexa__workspace_write", "held").map((o) => o.status)).toEqual(["done", "held", "running"]);
    expect(markOp(ops, undefined, "mcp__vexa__workspace_write", "error").map((o) => o.status)).toEqual(["done", "error", "running"]);
    expect(markOp(ops, "nope", "Bash", "error")).toBe(ops);
  });

  it("a commit's withheld files mark the steps that wrote them", () => {
    const ops = [{ ...toolOp("Write", { file_path: "/workspaces/oenb/README.md" }), status: "done" as const },
                 { ...toolOp("Write", { file_path: "/workspaces/oenb/notes.md" }), status: "done" as const }, read()];
    expect(markWithheld(ops, ["README.md"]).map((o) => o.status)).toEqual(["held", "done", "done"]);
    // a path that matches no step: every write of the turn is held (it is the commit that held them)
    expect(markWithheld(ops, ["elsewhere.md"]).map((o) => o.status)).toEqual(["held", "held", "done"]);
  });

  it("the server's held fields make the approval card", () => {
    expect(heldApproval({ code: "a8nv7c", workspace: "oenb", workspace_name: "OeNB × Vexa pilot", path: "README.md",
                          readers: ["ana"], sources: ["gmail"] }))
      .toEqual({ code: "a8nv7c", workspace: "oenb", name: "OeNB × Vexa pilot", path: "README.md", readers: ["ana"], sources: ["gmail"] });
    expect(heldApproval({ code: "" })).toBeNull();
  });

  it("a reloaded turn keeps the outcome", () => {
    expect(historyOp({ label: "edit", file: "/workspaces/oenb/README.md", wrote: true, outcome: "held" }).status).toBe("held");
    expect(historyOp({ label: "edit", file: "/workspaces/oenb/README.md", wrote: true, outcome: "failed" }).status).toBe("error");
    expect(historyOp({ label: "edit", file: "/workspaces/oenb/README.md", wrote: true }).status).toBe("done");
  });
});
