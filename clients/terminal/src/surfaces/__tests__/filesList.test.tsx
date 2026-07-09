/** Behavioral test for the Knowledge rail's workspaces-first layout (workspace.tsx FilesList).
 *
 *  Owner-approved direction (2026-07-09): WORKSPACES lead the rail (section on top, default OPEN);
 *  the FILES section defaults to COLLAPSED — only its header + the Find-file bar show — and expands
 *  on the caret, labeled with the home workspace; opening Knowledge lands on the home workspace's
 *  README (a preview via openPreview, slug-addressed), never a bare tree.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { FilesList } from "../workspace";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService, type LayoutService } from "../../workbench/layout";
import * as api from "../workspaceApi";

vi.mock("../workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../workspaceApi")>()),
  readActiveSet: vi.fn(),
  listWorkspaceTree: vi.fn(),
  readAttachedWorkspaces: vi.fn(),
  listSharedMemberships: vi.fn(),
  readWorkspaceGit: vi.fn(),
}));

const activeSet = {
  subject: "u1",
  active: [{ slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true, name: "leo" }],
} as unknown as Awaited<ReturnType<typeof api.readActiveSet>>;
const view = {
  active: "leo",
  slots: { leo: { repo: null, ref: null, name: "leo" } },
} as unknown as Awaited<ReturnType<typeof api.readAttachedWorkspaces>>;

let layout: LayoutService;
function renderList() {
  layout = createLayoutService("files");
  vi.spyOn(layout, "openPreview");
  render(
    <ServicesProvider container={createContainer([reg(LayoutServiceId, () => layout)])}>
      <FilesList />
    </ServicesProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(api.readActiveSet).mockResolvedValue(activeSet);
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md", "kg/index.md"]);
  vi.mocked(api.readAttachedWorkspaces).mockResolvedValue(view);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([]);
  vi.mocked(api.readWorkspaceGit).mockResolvedValue({ branch: "", changes: [], commits: [] } as Awaited<ReturnType<typeof api.readWorkspaceGit>>);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("FilesList — workspaces-first Knowledge rail", () => {
  it("leads with the WORKSPACES section, open by default", async () => {
    renderList();
    expect(screen.getByText("workspaces")).toBeTruthy();
    await screen.findByText("leo");           // the workspace row is visible without any toggle
    expect(screen.getByText(/Attach repo…/)).toBeTruthy();
  });

  it("keeps FILES collapsed by default — Find-file visible, tree hidden — and expands on the caret", async () => {
    renderList();
    expect(screen.getByPlaceholderText("Find file…")).toBeTruthy();
    await screen.findByText(/files · leo/);   // header carries the home workspace label
    expect(screen.queryByText("kg")).toBeNull();          // tree hidden while collapsed
    fireEvent.click(screen.getByText(/files · leo/));
    await screen.findByText("kg");                        // caret expands the home tree
    expect(sessionStorage.getItem("ws.files.open")).toBe("1");
  });

  it("lands on the home workspace's README as a slug-addressed preview", async () => {
    renderList();
    await waitFor(() => expect(layout.openPreview).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "doc", params: { path: "README.md", slug: "leo" } }),
    ));
    expect(vi.mocked(layout.openPreview).mock.calls.length).toBe(1);  // once per mount, not per poll
  });

  it("skips the README landing when a doc is already active (same-gesture open)", async () => {
    layout = createLayoutService("files");
    layout.setActiveTab({ kind: "doc", params: { path: "kg/index.md" } });
    vi.spyOn(layout, "openPreview");
    render(
      <ServicesProvider container={createContainer([reg(LayoutServiceId, () => layout)])}>
        <FilesList />
      </ServicesProvider>,
    );
    await screen.findByText(/files · leo/);
    expect(layout.openPreview).not.toHaveBeenCalled();
  });
});
