/** blocks.v1 `flow`: a routine or an automation proposal drawn as a small read-only graph, in chat,
 *  in a page and on the home — the same renderer the routine strip uses. */
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { sanitizeFence, type Block } from "../spec";
import { VexaFence } from "../Blocks";

afterEach(cleanup);
const golden = readFileSync(join(__dirname, "../../../../../../core/agent/contracts/blocks.v1/golden/Fence.flow.json"), "utf8");

describe("the flow block", () => {
  it("is read from a fence, capped, and keeps only step names and a 5-field cron", () => {
    const f = sanitizeFence(JSON.parse(golden));
    const b = f?.blocks.find((x) => x.type === "flow") as Extract<Block, { type: "flow" }>;
    expect(b.flow.trigger).toEqual({ kind: "schedule", cron: "*/15 * * * *" });
    expect(b.flow.steps).toEqual(["agent_run"]);
    const bad = sanitizeFence({ v: 1, blocks: [{ type: "flow", flow: { name: "x", trigger: { kind: "schedule", cron: "nope" }, steps: ["agent_run"] } },
                                              { type: "flow", flow: { name: "y", trigger: { kind: "event", event: "e" }, steps: ["<script>"] } }] });
    expect(bad?.blocks ?? []).toEqual([]);
  });
  it("draws the graph", () => {
    const { container } = render(<VexaFence source={golden} />);
    expect(container.querySelector('[data-flow-graph="unanswered-inbox-sweep"]')).not.toBeNull();
    expect(container.querySelector('[data-node="step:agent_run"]')).not.toBeNull();
  });
});
