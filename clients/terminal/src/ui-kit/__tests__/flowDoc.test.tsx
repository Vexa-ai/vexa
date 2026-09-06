/** A GENERATED FLOW PAGE OPENS WITH WHAT IT IS (Vexa-ai/vexa#1626).
 *
 *  `_global/flows/<flow>.md` declares `kind: flow` and carries its trigger, its version and its
 *  step count in front matter; the rules it honours are links into `POLICIES.md` in its own summary
 *  table. Every renderer stripped that block, so a page the Navigator now lists opened as prose
 *  with no header at all.
 *
 *  Four claims:
 *    1. the header renders from the file — trigger, steps, version, rules — and the page still does;
 *    2. the rules come out of the body's own links, so a rule reachable from the page is named here;
 *    3. a flow that honours none says so, rather than rendering an empty run of commas;
 *    4. an ordinary page is untouched: no header, front matter still stripped.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MdxDoc } from "../MdxDoc";
import { FLOW_KIND, declaredKind, flowRules, splitFrontmatter } from "../policyDoc";

// The chips resolve against a workspace snapshot; nothing here is about them, so the world is empty.
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

afterEach(cleanup);

// Shaped exactly like `behavior/global/flows/post_meeting.md`, which `make flow-pages` writes.
const FLOW = `---
kind: flow
flow: post_meeting
version: 4
trigger: meeting.completed
steps: 4
generated: from the code that runs it — edits here are overwritten
---

# post_meeting

Runs when **\`meeting.completed\`** happens, in 4 steps.

| | |
|---|---|
| **trigger** | \`meeting.completed\` |
| **version** | 4 — a step list changes by adding a version, never by editing one in place |
| **mails** | \`(composed in the step, from no template)\`, \`attendee-head\` |
| **rules it honours** | [\`report_to_participants\`](../POLICIES.md#report_to_participants), [\`data_statement\`](../POLICIES.md#data_statement) |

## The steps, in order

### 1. \`process_meeting\`

ONE REAL AGENT TURN on session meet-<id>.

- **rules it honours:** [\`report_to_participants\`](../POLICIES.md#report_to_participants)
`;

const NO_RULES = FLOW.replace(
  "| **rules it honours** | [`report_to_participants`](../POLICIES.md#report_to_participants), [`data_statement`](../POLICIES.md#data_statement) |",
  "| **rules it honours** | none |",
);

// ── 1 · what the page declares ───────────────────────────────────────────────────────────────

describe("a flow page declares what it is", () => {
  it("`kind: flow`, parsed from its own front matter", () => {
    const { attrs, body } = splitFrontmatter(FLOW);
    expect(declaredKind(attrs)).toBe(FLOW_KIND);
    expect(attrs).toContainEqual(["trigger", "meeting.completed"]);
    expect(attrs).toContainEqual(["steps", "4"]);
    expect(body.startsWith("# post_meeting")).toBe(true);
  });
});

// ── 2 · the rules come out of the file ───────────────────────────────────────────────────────

describe("flowRules", () => {
  it("reads the summary row's links, in order, once each", () => {
    expect(flowRules(FLOW)).toEqual(["report_to_participants", "data_statement"]);
  });

  it("`none` is an empty list, not a missing one", () => {
    expect(flowRules(NO_RULES)).toEqual([]);
  });

  it("a page with no such row asks nothing of the reader", () => {
    expect(flowRules("# just prose\n")).toEqual([]);
  });
});

// ── 3 · the header renders, and so does the page ─────────────────────────────────────────────

describe("the rendered header", () => {
  it("carries the trigger, the steps, the version and the rules", async () => {
    const { container } = render(<MdxDoc>{FLOW}</MdxDoc>);
    const head = container.querySelector('[data-flow-header="post_meeting"]')!;
    expect(head).toBeTruthy();
    const text = head.textContent ?? "";
    expect(text).toContain("meeting.completed");
    expect(text).toContain("post_meeting");
    expect(text).toContain("edits here are overwritten");
    expect([...head.querySelectorAll("[data-flow-rule]")].map((n) => n.getAttribute("data-flow-rule")))
      .toEqual(["report_to_participants", "data_statement"]);
    // the prose is still the page
    await waitFor(() => expect(container.textContent).toContain("The steps, in order"));
    // …and the front matter is not in it as body copy
    expect(container.textContent).not.toContain("kind: flow");
  });

  it("says `none` when the flow honours no rule", () => {
    const { container } = render(<MdxDoc>{NO_RULES}</MdxDoc>);
    const head = container.querySelector("[data-flow-header]")!;
    expect(head.querySelectorAll("[data-flow-rule]")).toHaveLength(0);
    expect(head.textContent).toContain("none");
  });
});

// ── 4 · every other page is untouched ────────────────────────────────────────────────────────

describe("an ordinary page", () => {
  it("gets no header, and still loses its front matter", async () => {
    const { container } = render(<MdxDoc>{"---\ntitle: Notes\n---\n\n# Notes\n\nBody.\n"}</MdxDoc>);
    expect(container.querySelector("[data-flow-header]")).toBeNull();
    expect(container.querySelector("[data-policy-rules]")).toBeNull();
    await waitFor(() => expect(container.textContent).toContain("Body."));
    expect(container.textContent).not.toContain("title: Notes");
  });
});
