/** THE POLICIES PAGE'S OWN ACT — **Set up policies** (Vexa-ai/vexa#1627).
 *
 *  Founder, 2026-09-06: the policy set is *"a tradeoff between adoption and security, but with
 *  specific risks that we can assess and define"*. The page already showed what this deployment
 *  answers; what it did not show was the way to arrive at a different answer. So its header carries
 *  the act that starts the wizard.
 *
 *  Four claims:
 *    1. the act appears in the `kind: policies` header, beside the profile, and NOWHERE else —
 *       an ordinary page must not sprout it;
 *    2. it names the page it was rendered in (`DocMetaContext`), never a constant this module keeps
 *       in step with the seed by hand (F63, one renderer along);
 *    3. a build with nothing registered shows the rules and no act — the rules are the content, and
 *       there is no conversation to start in a build with no chat in it;
 *    4. pressing it posts the `policies_wizard` intent and nothing else: no landing (the wizard's
 *       first turn writes nothing), no job (it is a conversation), and a plain-language fallback
 *       that still carries the shape when the ask is not in this library yet.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, waitFor, fireEvent } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { registry, type TabProps } from "../../contributions";
import { normalizeIntent, isPageIntent, type ChatIntent } from "../../surfaces/chatIntent";
import { isJobIntent } from "../../surfaces/jobs";
import { MdxDoc } from "../../ui-kit/MdxDoc";
import { DocMetaContext } from "../../ui-kit/docLinks";
import { POLICY_ACT_KIND } from "../../ui-kit/policyDoc";
import { clearPending, compactLabel, fallbackText, pendingLanding } from "../extend";
import { POLICIES_PATH, POLICIES_WORKSPACE, SET_UP_POLICIES, SetUpPoliciesButton } from "../PoliciesAct";

vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

const POLICIES = `---
kind: policies
profile: bank
open_web: off
---

# Policies

<a id="open_web"></a>
### \`open_web\` — an agent may fetch from the open web

**Default \`on\`.**

**Adoption.** Most of the difference between a note-taker and an assistant.
**Security.** It is an outbound path from inside the network. **Adversarial.** A fetched page is
untrusted text, and a URL an agent is talked into fetching is an SSRF attempt.
`;

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

describe("the act in the policy page's header", () => {
  const seen: TabProps[] = [];

  beforeEach(() => {
    seen.length = 0;
    registry.registerTab(POLICY_ACT_KIND, (p: TabProps) => {
      seen.push(p);
      return <button data-testid="setup">{SET_UP_POLICIES}</button>;
    });
  });

  it("stands in the header beside the profile, on a page that declares kind: policies", async () => {
    const { container, getByTestId } = render(<MdxDoc>{POLICIES}</MdxDoc>);
    await waitFor(() => expect(container.querySelector("[data-policy-rules]")).toBeTruthy());
    const header = container.querySelector("[data-policy-rules]")!.firstElementChild!;
    expect(header.textContent).toContain("bank");
    expect(header.contains(getByTestId("setup"))).toBe(true);
  });

  it("names the page it was rendered in, not a constant", async () => {
    render(
      <DocMetaContext.Provider value={{ slug: "_global", path: "POLICIES.md" }}>
        <MdxDoc>{POLICIES}</MdxDoc>
      </DocMetaContext.Provider>,
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0].params).toMatchObject({ workspace: "_global", path: "POLICIES.md" });
  });

  it("never appears on an ordinary page", async () => {
    const { container, queryByTestId } = render(<MdxDoc>{"---\ntype: meeting\n---\n\n# Sync\n\nwe shipped it"}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("we shipped it"));
    expect(queryByTestId("setup")).toBeNull();
    expect(seen).toHaveLength(0);
  });
});

// ── 3 · a build with nothing registered ──────────────────────────────────────────────────────

describe("a build with no shell to start a conversation in", () => {
  it("shows the rules and no act — silently, because the rules are the content", async () => {
    registry.registerTab(POLICY_ACT_KIND, undefined as unknown as (p: TabProps) => null);
    const { container } = render(<MdxDoc>{POLICIES}</MdxDoc>);
    await waitFor(() => expect(container.querySelector("[data-policy-rules]")).toBeTruthy());
    expect(container.querySelector('[data-policy-rule="open_web"]')).toBeTruthy();
    expect(container.textContent).not.toContain("not available in this build");
    expect(container.querySelector('[data-doc-act="policies-wizard"]')).toBeNull();
  });
});

// ── 4 · what the press means ─────────────────────────────────────────────────────────────────

describe("pressing it", () => {
  it("posts the wizard intent for the page it was given", () => {
    const { container } = render(<SetUpPoliciesButton workspace="_global" path="POLICIES.md" />);
    fireEvent.click(container.querySelector('[data-doc-act="policies-wizard"]')!);
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "policies_wizard", workspace: "_global", path: "POLICIES.md" });
    expect(asks[0].display).toBe("Set up policies: POLICIES.md");
    expect(asks[0].hidden).toBeUndefined();       // the person pressed it; they see it
  });

  it("falls back to the one file this act exists for when it is handed nothing", () => {
    const { container } = render(<SetUpPoliciesButton />);
    fireEvent.click(container.querySelector('[data-doc-act="policies-wizard"]')!);
    expect(asks[0].intent).toEqual({
      kind: "policies_wizard", workspace: POLICIES_WORKSPACE, path: POLICIES_PATH,
    });
  });

  it("lands nowhere — the wizard's first turn asks a question and writes nothing", () => {
    const { container } = render(<SetUpPoliciesButton workspace="_global" path="POLICIES.md" />);
    fireEvent.click(container.querySelector('[data-doc-act="policies-wizard"]')!);
    expect(pendingLanding()).toBeNull();
    expect(isPageIntent(asks[0].intent!)).toBe(false);
    expect(isJobIntent(asks[0].intent!)).toBe(false);
  });
});

// ── the intent itself, without a DOM ─────────────────────────────────────────────────────────

describe("the wizard intent (F63 — never a guessed path)", () => {
  it("carries the workspace and the path it was given", () => {
    expect(normalizeIntent({ kind: "policies_wizard", workspace: "_global", path: "POLICIES.md" }))
      .toEqual({ kind: "policies_wizard", workspace: "_global", path: "POLICIES.md" });
  });

  it("refuses a path that is missing or walks out of its mount", () => {
    expect(normalizeIntent({ kind: "policies_wizard", path: "" })).toBeNull();
    expect(normalizeIntent({ kind: "policies_wizard", path: "   " })).toBeNull();
    expect(normalizeIntent({ kind: "policies_wizard", path: "../etc/POLICIES.md" })).toBeNull();
  });

  it("an absent workspace stays absent, never an empty string", () => {
    const i = normalizeIntent({ kind: "policies_wizard", path: "POLICIES.md" })!;
    expect("workspace" in i).toBe(false);
  });

  it("reads back as the button, never as a paragraph the person did not write", () => {
    const i = normalizeIntent({ kind: "policies_wizard", workspace: "_global", path: "POLICIES.md" })!;
    expect(compactLabel(i)).toBe("Set up policies: POLICIES.md");
  });

  it("the fallback keeps the SHAPE when this library has no wizard ask yet", () => {
    const said = fallbackText(normalizeIntent({ kind: "policies_wizard", workspace: "_global", path: "POLICIES.md" })!);
    expect(said).toContain("_global/POLICIES.md");
    expect(said).toContain("FIVE questions, ONE AT A TIME");
    expect(said).toContain("naming the risk it assesses");
    expect(said).toContain("never from memory");
    expect(said).toContain("APPEND a `## Decision` section");
    expect(said).toContain("Never rewrite an older decision");
  });
});
