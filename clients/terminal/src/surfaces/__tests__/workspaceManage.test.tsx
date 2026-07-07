/** Smoke tests for the Workspace MANAGE panel (workspaceManage.tsx) — the center-tab hub.
 *  Locks that the four sections render, the GitHub section surfaces push/pull for a workspace with a
 *  home + ahead/behind, and a shared workspace shows its members + a Leave affordance.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import * as api from "../workspaceApi";

vi.mock("../workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../workspaceApi")>()),
  readAttachedWorkspaces: vi.fn(),
  readActiveSet: vi.fn(),
  listSharedMemberships: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspacePurpose: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  pushWorkspace: vi.fn(),
}));

// import AFTER the mock is registered (importing the module also registers the "workspace" tab-kind)
import { manageTabDescriptor } from "../workspaceManage";
import { registry } from "../../contributions";

const container = () => createContainer([reg(LayoutServiceId, () => createLayoutService("files"))]);
const renderPanel = (params: Record<string, unknown>) => {
  const Comp = registry.tabComponent("workspace")!;  // the panel registered as the "workspace" tab-kind
  return render(<ServicesProvider container={container()}><Comp id="workspace:x" params={params} active /></ServicesProvider>);
};

beforeEach(() => {
  // seed slot is a different workspace, so `acme-1234` is a NON-seed own slot (archive/delete allowed).
  vi.mocked(api.readAttachedWorkspaces).mockResolvedValue({ active: "seed", slots: { "acme-1234": { repo: "https://github.com/me/acme.git", ref: "main", name: "ACME deal" } } } as never);
  vi.mocked(api.readActiveSet).mockResolvedValue({ subject: "u1", active: [{ slug: "acme-1234", repo: null, ref: null, role: "private", path: "/w", write: true, primary: true }] } as never);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([]);
  vi.mocked(api.readWorkspacePurpose).mockResolvedValue("The ACME deal room.");
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
  it("renders header, purpose, GitHub (push/pull + ahead), participants CTA, danger zone", async () => {
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    expect(await screen.findByText("ACME deal")).toBeTruthy();          // header name
    expect(await screen.findByText("The ACME deal room.")).toBeTruthy(); // purpose loaded
    expect(screen.getByText("Purpose")).toBeTruthy();
    expect(screen.getByText("GitHub")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Push/)).toBeTruthy());  // has_home → push/pull
    expect(screen.getByText(/Pull/)).toBeTruthy();
    expect(screen.getByText("↑2")).toBeTruthy();                         // ahead count surfaced
    expect(screen.getByText("Share this workspace")).toBeTruthy();       // not shared yet → CTA
    expect(screen.getByText("Danger zone")).toBeTruthy();
  });

  it("Push reveals a token field and pushes with the entered token", async () => {
    vi.mocked(api.pushWorkspace).mockResolvedValue({ remote: "origin", url: "u", branch: "main", head_sha: "abc" } as never);
    renderPanel({ slug: "acme-1234", shared: false, name: "ACME deal" });
    await waitFor(() => screen.getByText(/Push/));
    fireEvent.click(screen.getByText(/Push/).closest("button")!);
    const tok = await screen.findByPlaceholderText(/GitHub token/);
    fireEvent.change(tok, { target: { value: "ghp_test" } });
    fireEvent.keyDown(tok, { key: "Enter" });
    await waitFor(() => expect(api.pushWorkspace).toHaveBeenCalledWith({ slug: "acme-1234", token: "ghp_test" }));
  });
});

describe("WorkspaceManagePanel — a shared workspace (member view)", () => {
  it("lists members + a Leave affordance", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "deal-9", role: "contributor" }] as never);
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: "main", tracked: false, ahead: 0, behind: 0 } as never);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
      { subject: "u_owner", role: "owner" }, { subject: "u_me", role: "contributor" },
    ] as never);
    renderPanel({ slug: "deal-9", shared: true, name: "deal-9" });
    expect(await screen.findByText("Participants")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("creator")).toBeTruthy());  // owner role → "creator" badge
    expect(screen.getByText("member")).toBeTruthy();                        // contributor → "member"
    expect(screen.getByText("Leave")).toBeTruthy();
  });
});
