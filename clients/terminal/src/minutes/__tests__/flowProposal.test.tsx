/** A STEP PROPOSAL'S OWN ACT — **Send to the developers** (Vexa-ai/vexa#1639).
 *
 *  Founder, 2026-09-06, in the governance chat of `_global`: *"we want to be able to write flows for
 *  the global chat as we like."* A flow is composed from step names this image already carries, so a
 *  sentence needing something no step does had nowhere to go — the agent could only refuse. It
 *  writes the step out as a page instead, under `_global/flows/proposals/`, and that page has
 *  exactly one thing to do with itself.
 *
 *  Five claims:
 *    1. the act appears in a `kind: proposal` header and NOWHERE else — an ordinary page must not
 *       sprout it, and neither must the policy page or a flow page;
 *    2. it names the page it was rendered in (`DocMetaContext`), never a constant (F63);
 *    3. a build with nothing registered shows the step and no act, silently — the step is the
 *       content, and a build with no chat has no conversation to start;
 *    4. pressing it posts the `flow_author` intent and NOTHING ELSE. It does not send: the ticket
 *       reaches humans at another company and cannot be withdrawn, so what the press opens is the
 *       turn that asks;
 *    5. the fallback sentence still carries the shape — one confirmation, the code fenced, and no
 *       names — for a library that does not have the ask yet.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, waitFor, fireEvent } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { registry, type TabProps } from "../../contributions";
import { normalizeIntent, isPageIntent, type ChatIntent } from "../../surfaces/chatIntent";
import { isJobIntent } from "../../surfaces/jobs";
import { MdxDoc } from "../../ui-kit/MdxDoc";
import { DocMetaContext } from "../../ui-kit/docLinks";
import { POLICY_ACT_KIND, PROPOSAL_ACT_KIND } from "../../ui-kit/policyDoc";
import { clearPending, compactLabel, fallbackText, pendingLanding } from "../extend";
import { SEND_TO_DEVELOPERS, SendToDevelopersButton } from "../FlowProposalAct";

vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

const PROPOSAL = `---
kind: proposal
step: drop_to_workspace
for-flow: post_meeting
trigger: meeting.completed
status: needs code — never executed
---

# drop_to_workspace

Put the meeting's report into one named workspace.
`;

const PATH = "flows/proposals/drop_to_workspace.md";

const asks: { prompt?: string; display?: string; intent?: ChatIntent; hidden?: boolean }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);

beforeEach(() => {
  asks.length = 0;
  clearPending();
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  cleanup();
});

// ── 1 + 2 · where the act appears, and what it is told about the page ─────────────────────────

describe("the act in a proposal page's header", () => {
  const seen: TabProps[] = [];

  beforeEach(() => {
    seen.length = 0;
    registry.registerTab(PROPOSAL_ACT_KIND, (p: TabProps) => {
      seen.push(p);
      return <button data-testid="send">{SEND_TO_DEVELOPERS}</button>;
    });
  });

  it("stands in the header beside the step it is about", async () => {
    const { container, getByTestId } = render(<MdxDoc>{PROPOSAL}</MdxDoc>);
    await waitFor(() => expect(container.querySelector("[data-proposal-header]")).toBeTruthy());
    const header = container.querySelector("[data-proposal-header]")!.firstElementChild!;
    expect(header.textContent).toContain("drop_to_workspace");
    expect(header.contains(getByTestId("send"))).toBe(true);
  });

  it("says the page is not something that runs", async () => {
    const { container } = render(<MdxDoc>{PROPOSAL}</MdxDoc>);
    await waitFor(() => expect(container.querySelector("[data-proposal-header]")).toBeTruthy());
    const head = container.querySelector("[data-proposal-header]")!.textContent ?? "";
    expect(head).toContain("proposal");
    expect(head).toContain("never executed");
    expect(head).toContain("post_meeting");
    expect(head).toContain("meeting.completed");
  });

  it("names the page it was rendered in, not a constant", async () => {
    render(
      <DocMetaContext.Provider value={{ slug: "_global", path: PATH }}>
        <MdxDoc>{PROPOSAL}</MdxDoc>
      </DocMetaContext.Provider>,
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0].params).toMatchObject({ workspace: "_global", path: PATH });
  });

  it("never appears on an ordinary page, on a flow page, or on the policy page", async () => {
    for (const src of [
      "---\ntype: meeting\n---\n\n# Sync\n\nwe shipped it",
      "---\nkind: flow\nflow: post_meeting\ntrigger: meeting.completed\nsteps: 4\n---\n\n# post_meeting\n\nwe shipped it",
      "---\nkind: policies\nprofile: bank\n---\n\n# Policies\n\nwe shipped it",
    ]) {
      const { container, queryByTestId } = render(<MdxDoc>{src}</MdxDoc>);
      await waitFor(() => expect(container.textContent).toContain("we shipped it"));
      expect(queryByTestId("send")).toBeNull();
      cleanup();
    }
    expect(seen).toHaveLength(0);
  });
});

it("a proposal page is never handed the POLICY page's act", async () => {
  registry.registerTab(POLICY_ACT_KIND, () => <button data-testid="setup">Set up policies</button>);
  const { container, queryByTestId } = render(<MdxDoc>{PROPOSAL}</MdxDoc>);
  await waitFor(() => expect(container.querySelector("[data-proposal-header]")).toBeTruthy());
  expect(queryByTestId("setup")).toBeNull();
});

// ── 3 · a build with nothing registered ──────────────────────────────────────────────────────

describe("a build with no shell to start a conversation in", () => {
  it("shows the step and no act — silently, because the step is the content", async () => {
    registry.registerTab(PROPOSAL_ACT_KIND, undefined as unknown as (p: TabProps) => null);
    const { container } = render(<MdxDoc>{PROPOSAL}</MdxDoc>);
    await waitFor(() => expect(container.querySelector("[data-proposal-header]")).toBeTruthy());
    expect(container.textContent).toContain("drop_to_workspace");
    expect(container.textContent).not.toContain("not available in this build");
    expect(container.querySelector('[data-doc-act="flow-proposal-send"]')).toBeNull();
  });
});

// ── 4 · what the press means ─────────────────────────────────────────────────────────────────

describe("pressing it", () => {
  it("posts the flow intent for the page it was given", () => {
    const { container } = render(<SendToDevelopersButton workspace="_global" path={PATH} />);
    fireEvent.click(container.querySelector('[data-doc-act="flow-proposal-send"]')!);
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "flow_author", workspace: "_global", path: PATH });
    expect(asks[0].display).toBe(`Write a flow: ${PATH}`);
    expect(asks[0].hidden).toBeUndefined();       // the person pressed it; they see it
  });

  it("lands nowhere and runs no job — what it opens is the question", () => {
    const { container } = render(<SendToDevelopersButton workspace="_global" path={PATH} />);
    fireEvent.click(container.querySelector('[data-doc-act="flow-proposal-send"]')!);
    expect(pendingLanding()).toBeNull();
    expect(isPageIntent(asks[0].intent!)).toBe(false);
    expect(isJobIntent(asks[0].intent!)).toBe(false);
  });

  it("renders nothing at all when it was handed no path", () => {
    const { container } = render(<SendToDevelopersButton workspace="_global" />);
    expect(container.querySelector('[data-doc-act="flow-proposal-send"]')).toBeNull();
    expect(asks).toHaveLength(0);
  });
});

// ── 5 · the intent, and what survives a library that has no ask yet ──────────────────────────

describe("the flow-author intent (F63 — never a guessed path)", () => {
  it("carries the workspace and the path it was given", () => {
    expect(normalizeIntent({ kind: "flow_author", workspace: "_global", path: PATH }))
      .toEqual({ kind: "flow_author", workspace: "_global", path: PATH });
  });

  it("refuses a path that is missing or walks out of its mount", () => {
    expect(normalizeIntent({ kind: "flow_author", path: "" })).toBeNull();
    expect(normalizeIntent({ kind: "flow_author", path: "   " })).toBeNull();
    expect(normalizeIntent({ kind: "flow_author", path: "../etc/passwd" })).toBeNull();
  });

  it("an absent workspace stays absent, never an empty string", () => {
    const i = normalizeIntent({ kind: "flow_author", path: "flows/README.md" })!;
    expect("workspace" in i).toBe(false);
  });

  it("reads back as the button, never as a paragraph the person did not write", () => {
    const i = normalizeIntent({ kind: "flow_author", workspace: "_global", path: PATH })!;
    expect(compactLabel(i)).toBe(`Write a flow: ${PATH}`);
  });

  it("the SEND fallback keeps the one confirmation, the code, and the no-names rule", () => {
    const said = fallbackText(normalizeIntent({ kind: "flow_author", workspace: "_global", path: PATH })!);
    expect(said).toContain(`_global/${PATH}`);
    expect(said).toContain("Confirm in ONE sentence");
    expect(said).toContain("Only if they say yes");
    expect(said).toContain("report_issue");
    expect(said).toContain("report_friction");
    expect(said).toContain("code fence verbatim");
    expect(said).toContain("NO names");
    expect(said).toContain("Never send the same proposal twice");
  });

  it("the AUTHORING fallback refuses to guess a step and keeps one confirmation", () => {
    const said = fallbackText(normalizeIntent({ kind: "flow_author", workspace: "_global", path: "flows/README.md" })!);
    expect(said).toContain("flows_list");
    expect(said).toContain("a name that is not in it does not exist here");
    expect(said).toContain("never a questionnaire");
    expect(said).toContain("ask ONE question — activate it? — and stop");
    expect(said).toContain("flows_submit");
    expect(said).toContain("_global/flows/<name>@<version>.md");
    expect(said).toContain("about ten seconds");
    expect(said).toContain("Editing is a NEW version");
    expect(said).toContain("never executed");
  });
});
