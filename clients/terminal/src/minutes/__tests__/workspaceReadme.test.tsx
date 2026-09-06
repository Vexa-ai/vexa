/** THE WORKSPACE README'S FRONT PAGE (Vexa-ai/vexa#1623).
 *
 *  Founder, 2026-09-06, on a customer workspace's `README.md` in the preview: *"if it's a workspace
 *  readme we want to have data: shared with whom, controls like github sync, git history lookup,
 *  etc."*
 *
 *  Rendered through `PagesPanel`, not as a loose component, because two of the three claims are
 *  about WHERE it appears — and a component tested on its own cannot fail the way the product does:
 *  by showing a workspace's membership above `drafts/plan.md`, or by not showing it at all on the
 *  one page it belongs to.
 *
 *  Three claims, each with a plausible wrong answer:
 *    · it stands on a workspace-root README and on no other page;
 *    · a READER of a workspace sees the data and the history and NOT ONE control — not a disabled
 *      one, not one that explains itself: a control whose only outcome is a 403 teaches a person the
 *      product is broken rather than that they lack the role;
 *    · the history renders as commits a person can read — author, time, message — and the page
 *      filter re-reads scoped to the open page.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import * as api from "../../surfaces/workspaceApi";

vi.mock("../../surfaces/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/workspaceApi")>()),
  readWorkspaceBySlug: vi.fn(),
  listWorkspaceTree: vi.fn(),
  readWorkspaceHistory: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspaceFile: vi.fn(),
  listSharedMemberships: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  readWorkspaceGitDiff: vi.fn(),
  removeWorkspaceMember: vi.fn(),
}));

import { PagesPanel } from "../PagesPanel";
import { boundSeries, countPages, isWorkspaceReadme, policySentence } from "../workspaceReadme";
import type { Page } from "../types";

/** The three sentences the panel lifts out of the seeded `behavior/global/POLICIES.md`, in the
 *  shapes that file actually uses (a bullet under *What is not yours to choose*, and two rule
 *  headings). Trimmed to what the parser reads; the real file is ~350 lines of the same shapes. */
const POLICIES = `---
kind: policies
global_admin_only: on
---

## What is not yours to choose

- a **participant** reads the transcript and the report of a meeting they were in;
- a **member** reads a group; an **owner or contributor** writes it;
- **\`_system\` is read by no agent for anybody else** — chats, sessions and settings are the one
  genuinely private tier, and no rule below can widen it;

<a id="agent_reads_desk"></a>
### \`agent_reads_desk\` — an agent may read its user's desk when its user is a participant

**Default \`on\`.**

### \`global_admin_only\` — only the admin writes \`_global\` (editors may be added)
`;

const COMMITS = [
  { sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", author: "126", kind: "you" as const, files: ["README.md"] },
  { sha: "00eb951", msg: "entity acme", when: "yesterday", author: "Ana", kind: "member" as const, files: ["kg/entities/acme.md"] },
];

const READ_ME: Page[] = [{ path: "README.md", slug: "oenb-b5e60c", label: "OeNB" }];

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={READ_ME} docPath="README.md" docSlug="oenb-b5e60c" onOpen={() => {}}
    body={"# OeNB\n\nThe workspace body."} {...over} />);

/** `/api/auth/me` (is this person the admin) and `/api/meetings` (what is bound here) are the two
 *  reads the panel makes outside `workspaceApi`. */
const serveFetch = (meetings: unknown[] = [], isAdmin = false) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    json: async () => (String(url).includes("/api/auth/me") ? { is_admin: isAdmin } : meetings),
  })) as unknown as typeof fetch);
};

beforeEach(() => {
  vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "OeNB", kind: "group", slug: "oenb-b5e60c", access: "readable", writable: false });
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md", "kg/entities/acme.md", "flows/post.md", ".git/config"]);
  vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "oenb-b5e60c", branch: "main", path: null, limit: 20, commits: COMMITS });
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: true, remote: "origin", url: "https://github.com/oenb/kg", branch: "main", tracked: true, ahead: 2, behind: 0 });
  vi.mocked(api.readWorkspaceFile).mockResolvedValue(POLICIES);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "oenb-b5e60c", role: "viewer" }]);
  vi.mocked(api.listWorkspaceMembers).mockRejectedValue(new Error("403"));
  serveFetch();
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

// ── pure rules ───────────────────────────────────────────────────────────────────────────────────
describe("which page is a workspace's front page", () => {
  it("the README at the ROOT is, and a README anywhere else is not", () => {
    expect(isWorkspaceReadme("README.md")).toBe(true);
    expect(isWorkspaceReadme("readme.md")).toBe(true);          // git is case-sensitive; people are not
    expect(isWorkspaceReadme("drafts/README.md")).toBe(false);  // a page about drafts
    expect(isWorkspaceReadme("kg/entities/acme.md")).toBe(false);
  });
});

describe("the policy sentence is READ from _global/POLICIES.md, never retyped", () => {
  it("one sentence per kind, in the file's own words", () => {
    expect(policySentence("group", POLICIES)).toBe("a member reads a group; an owner or contributor writes it");
    expect(policySentence("desk", POLICIES)).toBe("an agent may read its user's desk when its user is a participant");
    expect(policySentence("global", POLICIES)).toBe("only the admin writes _global (editors may be added)");
  });
  it("says nothing rather than something stale when the file cannot be read or has moved on", () => {
    expect(policySentence("group", null)).toBeNull();
    expect(policySentence("group", "# Policies\n\nrewritten past recognition\n")).toBeNull();
  });
});

describe("the counts and the bindings", () => {
  it("counts the pages a reader is shown — not machinery, not dotfiles, not non-pages", () => {
    expect(countPages(["README.md", "kg/entities/acme.md", "flows/post.md", ".git/config", "assets/logo.svg"])).toBe(2);
  });
  it("collapses the runs of one recurring invite into one thing the workspace is bound to", () => {
    const rows = [
      { id: 1, data: { workspace_id: "w", calendar_uid: "u1", title: "Tuesday sync" }, start_time: "2026-09-01" },
      { id: 2, data: { workspace_id: "w", calendar_uid: "u1", title: "Tuesday sync" }, start_time: "2026-09-08" },
      { id: 3, data: { workspace_id: "w", title: "One-off review" }, start_time: "2026-08-30" },
      { id: 4, data: { workspace_id: "other", calendar_uid: "u9", title: "Somebody else's" } },
    ];
    expect(boundSeries(rows, "w")).toEqual([
      { key: "cal:u1", title: "Tuesday sync", recurring: true, runs: 2, latest: "2026-09-08" },
      { key: "row:3", title: "One-off review", recurring: false, runs: 1, latest: "2026-08-30" },
    ]);
  });
});

// ── where it appears ─────────────────────────────────────────────────────────────────────────────
describe("the panel stands on a workspace README and nowhere else", () => {
  it("renders between the slug line and the body of a workspace-root README", async () => {
    const { container } = panel();
    const front = await screen.findByText("This workspace");
    expect(front).toBeTruthy();
    // between the crumb above and the prose below — the reading order is the claim
    const text = container.textContent ?? "";
    expect(text.indexOf("This workspace")).toBeLessThan(text.indexOf("The workspace body."));
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("group");
  });

  it("does NOT render on an ordinary page", async () => {
    const { container } = panel({
      pages: [{ path: "kg/entities/acme.md", slug: "oenb-b5e60c", label: "acme" }],
      docPath: "kg/entities/acme.md", body: "# Acme\n\nA company.",
    });
    await screen.findByText("A company.");
    expect(container.querySelector("[data-ws-readme]")).toBeNull();
    expect(api.readWorkspaceHistory).not.toHaveBeenCalled();
  });

  it("does not render while the README is being EDITED — an editor edits a file", async () => {
    const { container } = panel();
    await screen.findByText("This workspace");
    fireEvent.click(container.querySelector('[data-doc-act="edit"]')!);
    expect(container.querySelector("[data-ws-readme]")).toBeNull();
  });
});

// ── the data ─────────────────────────────────────────────────────────────────────────────────────
describe("the data a workspace README carries", () => {
  it("says what it is, where it is, how big it is, when it last changed, and who may read it", async () => {
    const { container } = panel();
    await screen.findByText("This workspace");
    const fact = (k: string) => container.querySelector(`[data-ws-fact="${k}"]`)?.textContent ?? "";
    expect(fact("kind")).toContain("Shared workspace");
    expect(fact("slug")).toContain("oenb-b5e60c");
    expect(fact("pages")).toContain("2");                       // machinery and dotfiles are not pages
    expect(fact("last")).toContain("readme: link the entity");
    expect(fact("last")).toContain("2 hours ago");
    expect(fact("policy")).toContain("a member reads a group");
  });

  it("names what it could not read instead of rendering a zero", async () => {
    vi.mocked(api.listWorkspaceTree).mockRejectedValue(new Error("boom"));
    const { container } = panel();
    await screen.findByText("This workspace");
    expect(container.querySelector('[data-ws-fact="pages"]')?.textContent).toContain("not readable");
    expect([...container.querySelectorAll("[data-ws-note]")].map((n) => n.textContent))
      .toContain("Could not count the pages.");
  });
});

// ── who may act ──────────────────────────────────────────────────────────────────────────────────
describe("a reader sees data and history, and no controls", () => {
  it("renders not one control for a viewer of the workspace", async () => {
    const { container } = panel();
    await screen.findByText("This workspace");
    await screen.findByText(/You are a reader here/);
    expect(container.querySelectorAll("[data-ws-act]")).toHaveLength(0);
    // …and the data is all there, which is the other half of the claim
    expect(container.querySelector('[data-ws-fact="kind"]')).toBeTruthy();
    expect(container.querySelector("[data-ws-history]")).toBeTruthy();
  });

  it("gives the OWNER the membership and the GitHub controls", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "oenb-b5e60c", role: "owner" }]);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
      { subject: "126", role: "owner", email: "dmitry@vexa.ai" },
      { subject: "77", role: "viewer", email: "ana@oenb.at" },
    ]);
    const { container } = panel();
    await screen.findByText("dmitry@vexa.ai");
    const acts = [...container.querySelectorAll("[data-ws-act]")].map((b) => b.getAttribute("data-ws-act"));
    expect(acts).toEqual(expect.arrayContaining(["invite", "sync", "pull", "push", "detach", "remove:77", "role:77"]));
    // the OWNER's own row carries no remove/role control — a workspace with no owner is not a state
    expect(acts).not.toContain("remove:126");
  });

  it("an owner-only act ARMS before it fires, and says what it will do", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "oenb-b5e60c", role: "owner" }]);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
      { subject: "126", role: "owner", email: "dmitry@vexa.ai" },
      { subject: "77", role: "viewer", email: "ana@oenb.at" },
    ]);
    const { container } = panel();
    await screen.findByText("ana@oenb.at");

    fireEvent.click(container.querySelector('[data-ws-act="remove:77"]')!);

    // the armed state SAYS what will happen, and the first click has done nothing
    expect(screen.getByText("Remove ana@oenb.at from this workspace")).toBeTruthy();
    expect(api.removeWorkspaceMember).not.toHaveBeenCalled();

    fireEvent.click(container.querySelector('[data-ws-act-cancel="remove:77"]')!);

    expect(container.querySelector('[data-ws-confirm="remove:77"]')).toBeNull();
    expect(api.removeWorkspaceMember).not.toHaveBeenCalled();

    // …and the second click is the one that acts
    fireEvent.click(container.querySelector('[data-ws-act="remove:77"]')!);
    fireEvent.click(container.querySelector('[data-ws-act-confirm="remove:77"]')!);
    await waitFor(() => expect(api.removeWorkspaceMember).toHaveBeenCalledWith("oenb-b5e60c", "77"));
  });
});

// ── the history ──────────────────────────────────────────────────────────────────────────────────
describe("git history lookup", () => {
  it("lists the workspace's commits with who, when and what", async () => {
    const { container } = panel();
    await screen.findByText("This workspace");
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));
    const rows = [...container.querySelectorAll("[data-ws-commit]")].map((r) => r.textContent ?? "");
    expect(rows[0]).toContain("7f6b769");
    expect(rows[0]).toContain("readme: link the entity");
    expect(rows[0]).toContain("126");
    expect(rows[1]).toContain("Ana");
  });

  it("the page filter re-reads the history scoped to the open page", async () => {
    const { container } = panel();
    await screen.findByText("This workspace");
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({
      slug: "oenb-b5e60c", branch: "main", path: "README.md", limit: 20, commits: [COMMITS[0]],
    });

    fireEvent.click(container.querySelector("[data-ws-history-filter]")!);

    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(1));
    expect(api.readWorkspaceHistory).toHaveBeenLastCalledWith("oenb-b5e60c", { path: "README.md", limit: 20 });
  });

  it("clicking a commit shows its diff, read-only", async () => {
    vi.mocked(api.readWorkspaceGitDiff).mockResolvedValue({ sha: "7f6b769", diff: "@@ -1 +1 @@\n-# OeNB\n+# OeNB\n+links" });
    const { container } = panel();
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));

    fireEvent.click(container.querySelector('[data-ws-commit="7f6b769"] button')!);

    await waitFor(() => expect(container.querySelector("[data-ws-diff]")?.textContent).toContain("+links"));
    expect(container.querySelector("[data-ws-diff]")!.tagName).toBe("PRE");   // shown, never editable
  });
});

// ── the desk and the company layer ───────────────────────────────────────────────────────────────
describe("the two workspaces that are not groups", () => {
  it("a DESK says who writes it and that the company's agents read it", async () => {
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "personal", branch: "main", path: null, limit: 20, commits: COMMITS });
    const { container } = panel({
      pages: [{ path: "README.md", label: "Desk", desk: true }], docSlug: undefined,
    });
    await screen.findByText("This workspace");
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("desk");
    expect(container.querySelector('[data-ws-members="desk"]')?.textContent).toContain("agents read it");
    expect(container.querySelector('[data-ws-fact="policy"]')?.textContent).toContain("an agent may read its user's desk");
    // the desk tab carries no slug, so the history route is addressed by the name the server keeps for it
    expect(api.readWorkspaceHistory).toHaveBeenCalledWith("personal", { limit: 20 });
  });

  it("the COMPANY LAYER says everybody reads and the admin writes — and offers a non-admin nothing", async () => {
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "_global", branch: "main", path: null, limit: 20, commits: COMMITS });
    const { container } = panel({
      pages: [{ path: "README.md", slug: "_global", label: "Company" }], docSlug: "_global",
    });
    await screen.findByText("This workspace");
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("global");
    expect(container.querySelector('[data-ws-members="global"]')?.textContent).toContain("administrator writes it");
    expect(container.querySelectorAll("[data-ws-act]")).toHaveLength(0);
  });
});
