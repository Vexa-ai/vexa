/** Smoke tests for the Workspace PAGE (workspaceManage.tsx) — the center tab a WORKSPACES row opens.
 *  Locks the README-first shape: the workspace README is the page body; management is a compact header
 *  (Share primary action + ⋯ menu with Archive/Delete) and the deeper sections (Purpose · GitHub ·
 *  Participants) fold behind one "Manage workspace" toggle.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
import * as api from "../../data/workspaceApi";

vi.mock("../../data/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../data/workspaceApi")>()),
  readAttachedWorkspaces: vi.fn(),
  readActiveSet: vi.fn(),
  listSharedMemberships: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspacePurpose: vi.fn(),
  readWorkspaceFile: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  pushWorkspace: vi.fn(),
  publishWorkspace: vi.fn(),
  shareEnableWorkspace: vi.fn(),
  mintInvite: vi.fn(),
}));

// import AFTER the mock is registered (importing the module also registers the "workspace" tab-kind)
import { manageTabDescriptor } from "../workspaceManage";
import { registry } from "../../contributions";

const container = () => createContainer([reg(LayoutServiceId, () => createLayoutService("files"))]);
const renderPanel = (params: Record<string, unknown>) => {
  const Comp = registry.tabComponent("workspace")!;  // the panel registered as the "workspace" tab-kind
  return render(<ServicesProvider container={container()}><Comp id="workspace:x" params={params} active /></ServicesProvider>);
};
const expandManage = async () => fireEvent.click(await screen.findByText("Manage workspace"));

beforeEach(() => {
  // seed slot is a different workspace, so `acme-1234` is a NON-seed own slot (archive/delete allowed).
  vi.mocked(api.readAttachedWorkspaces).mockResolvedValue({ active: "seed", slots: { "acme-1234": { repo: "https://github.com/me/acme.git", ref: "main", name: "ACME deal" } } } as never);
  vi.mocked(api.readActiveSet).mockResolvedValue({ subject: "u1", active: [{ slug: "acme-1234", repo: null, ref: null, role: "private", path: "/w", write: true, primary: true }] } as never);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([]);
  vi.mocked(api.readWorkspacePurpose).mockResolvedValue("The ACME deal room.");
  vi.mocked(api.readWorkspaceFile).mockResolvedValue("# ACME\n\nThe deal dashboard body.");
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: true, remote: "origin", url: "https://github.com/me/acme", branch: "main", tracked: true, ahead: 2, behind: 0 } as never);
  vi.mocked(api.listWorkspaceMembers).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("manageTabDescriptor", () => {
  it("own workspace → kind 'workspace', id by slug, slug param", () => {
    expect(manageTabDescriptor("acme-1234", { name: "ACME deal" })).toMatchObject({ kind: "workspace", id: "workspace:acme-1234", params: { slug: "acme-1234", shared: false } });
  });
  it("shared workspace → id namespaced + shared flag", () => {
    expect(manageTabDescriptor("wsid-9", { shared: true })).toMatchObject({ id: "workspace:shared:wsid-9", params: { slug: "wsid-9", shared: true } });
  });
});

describe("WorkspaceManagePanel — own workspace with a GitHub home", () => {
  it("renders the README as the page body, sync state in the meta row, sections folded", async () => {
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    expect(await screen.findByText("ACME deal")).toBeTruthy();                    // header name
    expect(await screen.findByText(/The deal dashboard body/)).toBeTruthy();      // README = the body
    expect(screen.getByText("↑2")).toBeTruthy();                                  // sync state in the meta row
    expect(screen.getByText("Share")).toBeTruthy();                               // primary header action
    expect(screen.queryByText("Purpose")).toBeNull();                             // sections folded by default
    expect(screen.queryByText("Repository")).toBeNull();
  });

  it("Manage workspace unfolds Purpose, Repository (push/pull), participants CTA", async () => {
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await expandManage();
    expect(await screen.findByText("The ACME deal room.")).toBeTruthy();  // purpose loaded
    expect(screen.getByText("Purpose")).toBeTruthy();
    expect(screen.getByText("Repository")).toBeTruthy();
    expect(await screen.findByText("Open on GitHub")).toBeTruthy();       // the home's provider, by label
    await waitFor(() => expect(screen.getByText(/Push/)).toBeTruthy());   // has_home → push/pull
    expect(screen.getByText(/Pull/)).toBeTruthy();
    expect(screen.getByText("Share this workspace")).toBeTruthy();        // not shared yet → CTA
  });

  it("⋯ menu carries Rename / Manage / Archive / Delete (the old Danger zone)", async () => {
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await screen.findByText("ACME deal");
    fireEvent.click(screen.getByTitle("Rename · Manage · Archive · Delete"));
    expect(screen.getByText("Rename")).toBeTruthy();
    expect(screen.getByText("Hide")).toBeTruthy();
    expect(screen.getByText("Delete")).toBeTruthy();
  });

  it("Push reveals a token field and pushes with the entered token", async () => {
    vi.mocked(api.pushWorkspace).mockResolvedValue({ remote: "origin", url: "u", branch: "main", head_sha: "abc" } as never);
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await expandManage();
    await waitFor(() => screen.getByText(/Push/));
    fireEvent.click(screen.getByText(/Push/).closest("button")!);
    const tok = await screen.findByPlaceholderText(/GitHub token/);
    fireEvent.change(tok, { target: { value: "ghp_test" } });
    fireEvent.keyDown(tok, { key: "Enter" });
    await waitFor(() => expect(api.pushWorkspace).toHaveBeenCalledWith({ slug: "acme-1234", token: "ghp_test" }));
  });

  it("a workspace WITHOUT a git home gets the Publish CTA, publishing with its slug", async () => {
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: "master", tracked: false, ahead: 0, behind: 0 } as never);
    vi.mocked(api.publishWorkspace).mockResolvedValue({ repo_url: "https://github.com/me/acme", pushed_ref: "master", head_sha: "abc", created: true } as never);
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await expandManage();
    fireEvent.click(await screen.findByText("Publish to GitHub…"));
    const tok = screen.getByPlaceholderText(/GitHub token/);
    fireEvent.change(tok, { target: { value: "ghp_x" } });
    fireEvent.click(screen.getByText("Publish"));
    // slug rides along so the backend publishes THIS workspace, not the seed
    await waitFor(() => expect(api.publishWorkspace).toHaveBeenCalledWith("acme-deal", true, "ghp_x", undefined, "acme-1234", { provider: "github", org: undefined }));
  });

  it("publishes to a chosen provider and a nested group, using that provider's saved token", async () => {
    const lists = {
      default: "github",
      providers: [
        { name: "github", kind: "github", label: "GitHub", base_url: "https://github.com", host: "github.com", nested_groups: false, can_create: true, token_saved: false, masked: null },
        { name: "corp", kind: "gitlab", label: "Corp GitLab", base_url: "https://git.example.com", host: "git.example.com", nested_groups: true, can_create: true, token_saved: true, masked: "••••abcd" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      new Response(JSON.stringify(String(url).includes("git-providers") ? lists : { set: false, masked: null }), { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: "master", tracked: false, ahead: 0, behind: 0 } as never);
    vi.mocked(api.publishWorkspace).mockResolvedValue({ repo_url: "https://git.example.com/it/ai/acme-deal", pushed_ref: "master", head_sha: "abc", created: true, provider: "corp" } as never);
    try {
      renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
      await expandManage();
      fireEvent.click(await screen.findByText("Publish to GitHub…"));
      const select = await screen.findByLabelText("Provider");
      expect(screen.getByPlaceholderText("GitHub token (used once, never stored)")).toBeTruthy();
      fireEvent.change(select, { target: { value: "corp" } });
      const org = screen.getByLabelText("Owner / group") as HTMLInputElement;
      expect(org.placeholder).toBe("group/subgroup");
      fireEvent.change(org, { target: { value: "it/ai" } });
      expect(screen.getByPlaceholderText("Corp GitLab token (optional — using your saved token)")).toBeTruthy();
      fireEvent.click(screen.getByText("Publish"));
      await waitFor(() => expect(api.publishWorkspace).toHaveBeenCalledWith("acme-deal", true, undefined, undefined, "acme-1234", { provider: "corp", org: "it/ai" }));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("header Share enables sharing and opens the invite MODAL (portaled — never lost below a long README)", async () => {
    vi.mocked(api.shareEnableWorkspace).mockResolvedValue({ workspace_id: "acme-ws-1" } as never);
    vi.mocked(api.mintInvite).mockResolvedValue({ token: "tok123" } as never);
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await screen.findByText("ACME deal");
    fireEvent.click(screen.getByText("Share").closest("button")!);
    await waitFor(() => expect(api.shareEnableWorkspace).toHaveBeenCalledWith("acme-1234"));
    const dialog = await screen.findByRole("dialog");                     // a real modal, not an inline row
    fireEvent.click(within(dialog).getByText("Create link"));
    await waitFor(() => expect(api.mintInvite).toHaveBeenCalledWith(expect.objectContaining({ workspace_id: "acme-ws-1", mode: "open" })));
    expect((within(dialog).getByDisplayValue(/invite=tok123/) as HTMLInputElement).value).toContain("tok123");  // copyable link minted in place
  });
});

describe("WorkspaceManagePanel — a shared workspace (member view)", () => {
  it("meta row shows membership; Manage lists members + a Leave affordance", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "deal-9", role: "contributor" }] as never);
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: "main", tracked: false, ahead: 0, behind: 0 } as never);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
      { subject: "u_owner", role: "owner" }, { subject: "u_me", role: "contributor" },
    ] as never);
    renderPanel({ slug: "deal-9", shared: true, name: "deal-9" });
    expect(await screen.findByText("2 members")).toBeTruthy();               // header meta row
    expect(screen.getByText(/shared · contributor/)).toBeTruthy();
    await expandManage();
    expect(await screen.findByText("Participants")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("creator")).toBeTruthy());   // owner role → "creator" badge
    expect(screen.getByText("member")).toBeTruthy();                         // contributor → "member"
    expect(screen.getByText("Leave")).toBeTruthy();
  });
});
