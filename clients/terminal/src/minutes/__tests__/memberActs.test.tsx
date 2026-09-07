/** ADDING A MEMBER IS A CONVERSATION (Vexa-ai/vexa#1632).
 *
 *  Founder, 2026-09-06, after pressing *"Add a member…"* on a group's front page and being answered
 *  with `invite role must be one of ('contributor',)`: *"this add member should just ask chat to do
 *  that with mcp, asking their emails etc."* — and, on what the page itself should carry: *"so we do
 *  not have to create UI here — button to trigger the chat."*
 *
 *  So the three membership controls stopped being routes and became QUESTIONS. What has to be true:
 *
 *    1. pressing one posts an act and touches NO API — the page mints nothing, which is the whole
 *       of the defect: the control that failed him was the one minting an invite with a role it had
 *       never asked anybody about;
 *    2. a row's act names the person that row is about, so the sentence the agent says back is about
 *       somebody the reader can see;
 *    3. a reader sees none of the three — rule 1 of the panel, unchanged by any of this;
 *    4. the intent refuses an act that names no workspace, and bounds the member it carries;
 *    5. the label and the fallback say the three things whose absence would be invisible: which
 *       workspace, which VERB the agent must reach for, and what each role actually grants.
 *
 *  The panel is rendered directly here rather than through `PagesPanel`, because unlike
 *  `workspaceReadme.test.tsx` none of these claims is about WHERE the panel appears — they are about
 *  what one press means.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import * as api from "../../surfaces/workspaceApi";
import { MEMBER_MAX, isMemberIntent, isPageIntent, normalizeIntent, type ChatIntent } from "../../surfaces/chatIntent";
import { isJobIntent } from "../../surfaces/jobs";

vi.mock("../../surfaces/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/workspaceApi")>()),
  readWorkspaceBySlug: vi.fn(),
  listWorkspaceTree: vi.fn(),
  readWorkspaceHistory: vi.fn(),
  readLastChange: vi.fn(),
  readMyPerson: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspaceFile: vi.fn(),
  listSharedMemberships: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  // The three the page used to call itself. Mocked so that "it called nothing" is a claim this file
  // can actually make, rather than an absence nobody watched.
  mintInvite: vi.fn(),
  removeWorkspaceMember: vi.fn(),
  setWorkspaceMemberRole: vi.fn(),
}));

import { clearPending, compactLabel, fallbackText, pendingLanding } from "../extend";
import { WorkspaceReadmePanel } from "../WorkspaceReadmePanel";

const SLUG = "pilot-b5e60c";

const asks: { prompt?: string; display?: string; intent?: ChatIntent; hidden?: boolean }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);

/** `/api/auth/me` and `/api/meetings` — the two reads the panel makes outside `workspaceApi`. */
let fetched: ReturnType<typeof vi.fn>;

beforeEach(() => {
  asks.length = 0;
  clearPending();
  window.localStorage.clear();
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
  vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "Pilot", kind: "group", slug: SLUG, access: "readable", writable: false });
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md"]);
  vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: SLUG, branch: "main", path: null, limit: 20, commits: [] });
  vi.mocked(api.readLastChange).mockResolvedValue({ slug: SLUG, path: null, change: null });
  vi.mocked(api.readMyPerson).mockResolvedValue({ subject: "126", name: null, first_name: null });
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: null, tracked: false, ahead: 0, behind: 0 });
  vi.mocked(api.readWorkspaceFile).mockResolvedValue(null);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: SLUG, role: "viewer" }]);
  vi.mocked(api.listWorkspaceMembers).mockRejectedValue(new Error("403"));
  fetched = vi.fn(async (url: string) => ({
    ok: true,
    json: async () => (String(url).includes("/api/auth/me") ? { is_admin: false } : []),
  }));
  vi.stubGlobal("fetch", fetched as unknown as typeof fetch);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear();
});

/** An owner, with one reader beside them on the roster. */
const asOwner = () => {
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: SLUG, role: "owner" }]);
  vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
    { subject: "126", role: "owner", email: "owner@example.com" },
    { subject: "77", role: "viewer", email: "jsmith@example.com" },
  ]);
};

/** Render the panel and open the PEOPLE section. One click: since #1634 the strip is two sentences
 *  with everything behind **History**, the one disclosure at the end of line two, and since #1642
 *  that disclosure opens the three sections themselves rather than a row of summaries. Nothing below
 *  it exists until a reader asks for it. */
const shared = async () => {
  const { container } = render(<WorkspaceReadmePanel slug={SLUG} path="README.md" />);
  const details = await waitFor(() => {
    const d = container.querySelector<HTMLButtonElement>("[data-ws-details]");
    if (!d) throw new Error("the strip has not answered yet");
    return d;
  });
  fireEvent.click(details);
  await waitFor(() => {
    const s = container.querySelector<HTMLElement>('[data-ws-section="people"]');
    if (!s) throw new Error("no people section behind the details yet");
    return s;
  });
  return container;
};

const act = (container: HTMLElement, id: string) => container.querySelector<HTMLButtonElement>(`[data-ws-act="${id}"]`);

// ── 1 + 2 · what a press means ───────────────────────────────────────────────────────────────────

describe("the three membership controls hand the act to the chat", () => {
  it("Add a member… posts the act naming this workspace, and mints nothing", async () => {
    asOwner();
    const container = await shared();
    await screen.findByText("jsmith@example.com");
    const before = fetched.mock.calls.length;

    fireEvent.click(act(container, "member-add")!);

    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "member_add", workspace: SLUG });
    expect(asks[0].display).toBe(`Add a member: ${SLUG}`);
    expect(asks[0].hidden).toBeUndefined();          // the person pressed it; they see it
    // THE DEFECT ITSELF: the page used to mint an invite with a role nobody had been asked about.
    expect(api.mintInvite).not.toHaveBeenCalled();
    expect(fetched.mock.calls.length).toBe(before);
    // no field appeared either — the agent's question is the field
    expect(container.querySelector("input")).toBeNull();
  });

  it("Change role and Remove name the member their row is about", async () => {
    asOwner();
    const container = await shared();
    await screen.findByText("jsmith@example.com");

    fireEvent.click(act(container, "member-role:77")!);
    expect(asks[0].intent).toEqual({ kind: "member_role", workspace: SLUG, member: "jsmith@example.com" });
    expect(asks[0].display).toBe(`Change role: ${SLUG} · jsmith@example.com`);

    fireEvent.click(act(container, "member-remove:77")!);
    expect(asks[1].intent).toEqual({ kind: "member_remove", workspace: SLUG, member: "jsmith@example.com" });
    expect(asks[1].display).toBe(`Remove a member: ${SLUG} · jsmith@example.com`);

    expect(api.setWorkspaceMemberRole).not.toHaveBeenCalled();
    expect(api.removeWorkspaceMember).not.toHaveBeenCalled();
  });

  it("ONE press is the whole control — it does not arm, and it lands nowhere", async () => {
    asOwner();
    const container = await shared();
    await screen.findByText("jsmith@example.com");

    fireEvent.click(act(container, "member-remove:77")!);

    // the confirmation moved to the chat (#1632), so there is no armed state here to confirm
    expect(container.querySelector('[data-ws-confirm="member-remove:77"]')).toBeNull();
    expect(container.querySelector("[data-ws-act-confirm]")).toBeNull();
    // …and no page to navigate to, and no job to watch: it opened a conversation
    expect(pendingLanding()).toBeNull();
    expect(isPageIntent(asks[0].intent!)).toBe(false);
    expect(isJobIntent(asks[0].intent!)).toBe(false);
  });

  it("the panel renders no receipt of its own — the sentence is the agent's now", async () => {
    asOwner();
    const container = await shared();
    await screen.findByText("jsmith@example.com");

    fireEvent.click(act(container, "member-remove:77")!);

    expect(container.querySelector("[data-ws-said]")).toBeNull();
  });
});

// ── 3 · who sees them ────────────────────────────────────────────────────────────────────────────

describe("a reader is offered none of the three", () => {
  it("renders no membership control for somebody who is not an owner here", async () => {
    const container = await shared();               // the default membership in this file is `viewer`
    await screen.findByText(/You are a reader here/);

    expect(act(container, "member-add")).toBeNull();
    expect(container.querySelector('[data-ws-act^="member-role:"]')).toBeNull();
    expect(container.querySelector('[data-ws-act^="member-remove:"]')).toBeNull();
    expect(asks).toHaveLength(0);
  });
});

// ── 4 · the intent, without a DOM ────────────────────────────────────────────────────────────────

describe("the member intent (F63 — never a guessed workspace)", () => {
  it("carries the workspace, and the member when there is one", () => {
    expect(normalizeIntent({ kind: "member_add", workspace: "pilot" }))
      .toEqual({ kind: "member_add", workspace: "pilot" });
    expect(normalizeIntent({ kind: "member_role", workspace: "pilot", member: "jsmith@example.com" }))
      .toEqual({ kind: "member_role", workspace: "pilot", member: "jsmith@example.com" });
  });

  it("REFUSES an act that names no workspace — these three change who may read and write one", () => {
    expect(normalizeIntent({ kind: "member_add", workspace: "" })).toBeNull();
    expect(normalizeIntent({ kind: "member_role", workspace: "   ", member: "jsmith@example.com" })).toBeNull();
    expect(normalizeIntent({ kind: "member_remove", member: "jsmith@example.com" })).toBeNull();
  });

  it("an absent member stays absent, never an empty string", () => {
    const i = normalizeIntent({ kind: "member_add", workspace: "pilot" })!;
    expect("member" in i).toBe(false);
    expect(normalizeIntent({ kind: "member_remove", workspace: "pilot", member: "  " })!)
      .toEqual({ kind: "member_remove", workspace: "pilot" });
  });

  it("flattens and caps the member — a name, never a pasted paragraph", () => {
    const flat = normalizeIntent({ kind: "member_role", workspace: "pilot", member: " jsmith@example.com \n  (J. Smith) " })!;
    expect(flat).toEqual({ kind: "member_role", workspace: "pilot", member: "jsmith@example.com (J. Smith)" });

    const long = normalizeIntent({ kind: "member_role", workspace: "pilot", member: "a".repeat(MEMBER_MAX + 200) })!;
    expect(long.member).toHaveLength(MEMBER_MAX);
  });

  it("is a member act and not a page one — the guard is what every consumer narrows on", () => {
    const i = normalizeIntent({ kind: "member_add", workspace: "pilot" })!;
    expect(isMemberIntent(i)).toBe(true);
    expect(isPageIntent(i)).toBe(false);
    expect(isMemberIntent(normalizeIntent({ kind: "extend", path: "kg/plan.md" })!)).toBe(false);
  });
});

// ── 5 · the label and the fallback ───────────────────────────────────────────────────────────────

describe("what the person reads, and what the agent reads", () => {
  it("the bubble is the verb, the workspace, and the person when there is one", () => {
    expect(compactLabel(normalizeIntent({ kind: "member_add", workspace: "pilot" })!))
      .toBe("Add a member: pilot");
    expect(compactLabel(normalizeIntent({ kind: "member_role", workspace: "pilot", member: "jsmith@example.com" })!))
      .toBe("Change role: pilot · jsmith@example.com");
    expect(compactLabel(normalizeIntent({ kind: "member_remove", workspace: "pilot", member: "jsmith@example.com" })!))
      .toBe("Remove a member: pilot · jsmith@example.com");
  });

  /** THE VERB IS WHAT MUST SURVIVE A LIBRARY THAT PREDATES THESE PRESETS. A fallback that described
   *  the act without naming the tool would leave the agent to reach for whatever membership-shaped
   *  thing it could find — and reaching for the page's own route is exactly the failure this act
   *  exists to remove. The three role words ride along for the same reason: a confirmation that
   *  names a role without saying what it grants is a yes to something unread. */
  const ROLES = ["`owner`", "`contributor`", "`reader`"];

  it("Add a member: ONE question, ONE sentence, then workspace_invite once per address", () => {
    const said = fallbackText(normalizeIntent({ kind: "member_add", workspace: "pilot" })!);
    expect(said).toContain('workspace_invite(slug="pilot", email=..., role=...)');
    for (const r of ROLES) expect(said).toContain(r);
    expect(said).toContain("an owner writes this group and can add or remove its members");
    expect(said).toContain("a contributor writes this group");
    expect(said).toContain("a reader reads this group and does not write it");
    expect(said).toContain("Ask ONE question");
    expect(said).toContain("confirm in ONE");
    expect(said).toContain("ONCE PER ADDRESS");
    expect(said).toContain("Never write a page for this");
  });

  it("Change role: names the member, asks one question, then workspace_membership", () => {
    const said = fallbackText(normalizeIntent({ kind: "member_role", workspace: "pilot", member: "jsmith@example.com" })!);
    expect(said).toContain('workspace_membership(slug="pilot", email=..., role=...)');
    expect(said).toContain("jsmith@example.com");
    for (const r of ROLES) expect(said).toContain(r);
    expect(said).toContain("ask ONE question");
    expect(said).toContain("confirm in ONE");
  });

  it("Remove: one sentence to be sure of, then workspace_membership with role=remove", () => {
    const said = fallbackText(normalizeIntent({ kind: "member_remove", workspace: "pilot", member: "jsmith@example.com" })!);
    expect(said).toContain('workspace_membership(slug="pilot", email=..., role="remove")');
    expect(said).toContain("jsmith@example.com");
    for (const r of ROLES) expect(said).toContain(r);
    expect(said).toContain("confirm in ONE sentence");
    // it asks nothing else — a removal has no field to fill in
    expect(said).toContain("Ask nothing else");
  });
});
