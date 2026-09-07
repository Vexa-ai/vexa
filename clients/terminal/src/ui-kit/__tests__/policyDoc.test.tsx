/** `_global/POLICIES.md` is the ONE page whose front matter is the content, and a generated flow
 *  page is the one whose appendix is Python.
 *
 *  Four claims:
 *    1. the block is parsed, not discarded, and only for a page that DECLARES it is a policy page;
 *    2. every rule the file answers appears, with its value, its default and its three lenses —
 *       all of them lifted out of the same file, none of them known to the renderer;
 *    3. an ordinary page renders exactly as it did — front matter stripped, nothing added;
 *    4. a flow page's view-source fold survives the MDX compile with the Python intact.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MdxDoc } from "../MdxDoc";
import { policyRuleDocs, splitFrontmatter } from "../policyDoc";

// The chips resolve against a workspace snapshot; nothing here is about them, so the world is empty.
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

afterEach(cleanup);

const POLICIES = `---
kind: policies
profile: default
agent_reads_desk: on
newcomer_reads_history: off
transcript_retention_days: forever
attendee_domains:
---

# Policies

<a id="agent_reads_desk"></a>
### \`agent_reads_desk\` — an agent may read its user's desk when its user is a participant

**Default \`on\`.**

**The effect.** In a post-meeting room, the turn mounts the desks of the people who were there.

**Adoption.** It is what makes a report worth opening rather than a set of minutes.
**Security.** It is the widest read in the product. **Adversarial.** Somebody who gets themselves
into a meeting gets an agent reading the other participants' desks.

**The price of turning it off.** Reports stop being personal.

<a id="newcomer_reads_history"></a>
### \`newcomer_reads_history\` — a newcomer to a series reads its earlier reports

**Default \`off\`.**

**Adoption.** Context on arrival. **Security.** It hands somebody meetings they were not in.
**Adversarial.** Getting added to a standing invite is easier than getting into one meeting.
`;

// ── 1 · parsed, and only when the page says it is one ────────────────────────────────────────

describe("splitFrontmatter", () => {
  it("hands back the block and the body", () => {
    const { attrs, body } = splitFrontmatter(POLICIES);
    expect(attrs[0]).toEqual(["kind", "policies"]);
    expect(Object.fromEntries(attrs).agent_reads_desk).toBe("on");
    expect(Object.fromEntries(attrs).attendee_domains).toBe("");
    expect(body.startsWith("# Policies")).toBe(true);
  });

  it("a page with no fence is all body", () => {
    expect(splitFrontmatter("# Hello\n\nworld")).toEqual({ attrs: [], body: "# Hello\n\nworld" });
  });

  it("a fence that never closes is not front matter", () => {
    const md = "---\nkind: policies\n\nprose";
    expect(splitFrontmatter(md).attrs).toEqual([]);
    expect(splitFrontmatter(md).body).toBe(md);
  });
});

it("renders the rule list for a page that declares kind: policies", async () => {
  const { container } = render(<MdxDoc>{POLICIES}</MdxDoc>);
  await waitFor(() => expect(container.querySelector("[data-policy-rules]")).toBeTruthy());
  expect(container.querySelector('[data-policy-rule="agent_reads_desk"]')).toBeTruthy();
  expect(container.querySelector('[data-policy-rule="newcomer_reads_history"]')).toBeTruthy();
  // `kind` and `profile` are not rules; the profile is shown as what it is
  expect(container.querySelector('[data-policy-rule="kind"]')).toBeNull();
  expect(container.querySelector('[data-policy-rule="profile"]')).toBeNull();
});

it("an ordinary page still has its front matter stripped and nothing added", async () => {
  const { container } = render(<MdxDoc>{"---\ntype: meeting\n---\n\n# Pilot sync\n\nwe shipped it"}</MdxDoc>);
  await waitFor(() => expect(container.textContent).toContain("we shipped it"));
  expect(container.textContent).not.toContain("type: meeting");
  expect(container.querySelector("[data-policy-rules]")).toBeNull();
});

// ── 2 · every word comes out of the file ─────────────────────────────────────────────────────

describe("policyRuleDocs", () => {
  it("lifts the default and the three lenses out of each rule's own section", () => {
    const docs = policyRuleDocs(splitFrontmatter(POLICIES).body);
    expect(docs.agent_reads_desk.fallback).toBe("Default `on`");
    expect(docs.agent_reads_desk.adoption).toContain("worth opening");
    expect(docs.agent_reads_desk.security).toContain("widest read");
    expect(docs.agent_reads_desk.adversarial).toContain("reading the other participants' desks");
    expect(docs.newcomer_reads_history.fallback).toBe("Default `off`");
  });

  it("a rule with no section degrades to nothing rather than to a guess", () => {
    const docs = policyRuleDocs("# Policies\n\nnothing anchored here");
    expect(docs.open_web).toBeUndefined();
  });
});

it("shows each rule's value, its default and its lenses", async () => {
  const { container } = render(<MdxDoc>{POLICIES}</MdxDoc>);
  await waitFor(() => expect(container.querySelector("[data-policy-rules]")).toBeTruthy());
  const row = container.querySelector('[data-policy-rule="agent_reads_desk"]')!;
  expect(row.textContent).toContain("on");
  expect(row.textContent).toContain("Default `on`");
  expect(row.textContent?.toLowerCase()).toContain("adoption");
  expect(row.textContent?.toLowerCase()).toContain("security");
  expect(row.textContent?.toLowerCase()).toContain("adversarial");
  const unanswered = container.querySelector('[data-policy-rule="attendee_domains"]')!;
  expect(unanswered.textContent).toContain("unset");
});

// ── 4 · the view-source fold on a generated flow page ────────────────────────────────────────

const FLOW_PAGE = `---
kind: flow
flow: post_meeting
version: 4
---

# post_meeting

Runs when \`meeting.completed\` happens.

<ViewSource step="email_attendees">

\`\`\`python
@reg.step(needs=("agent", "meetings"))
def email_attendees(ctx: StepCtx):
    if a < b and {"x": 1}:
        return Done({})
\`\`\`

</ViewSource>
`;

it("a flow page's view-source fold survives the compile with the Python intact", async () => {
  const { container } = render(<MdxDoc>{FLOW_PAGE}</MdxDoc>);
  await waitFor(() => expect(container.textContent).toContain("post_meeting"));
  const fold = container.querySelector("[data-view-source]");
  expect(fold).toBeTruthy();
  expect(fold?.querySelector("summary")?.textContent).toContain("view source");
  // The Python is verbatim: the angle bracket and the braces are code, not markup, and they must
  // not have been escaped on the way through the compile.
  const code = fold?.querySelector("pre")?.textContent ?? "";
  expect(code).toContain("def email_attendees(ctx: StepCtx):");
  expect(code).toContain('if a < b and {"x": 1}:');
  // and it is NOT the plain-Markdown fallback (which would say so in a line of its own)
  expect(container.textContent).not.toContain("simplified rendering");
});
