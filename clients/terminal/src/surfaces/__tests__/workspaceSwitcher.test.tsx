/** Behavioral test for the WORKSPACES sidebar section (workspace.tsx WorkspaceSwitcher).
 *
 *  Owner ruling (start-fresh placement): "start fresh" CREATES a new default workspace (the current
 *  one is parked as '(previous)'), so it is a LIST-LEVEL item alongside "+ Attach repo…" — NOT a ↻
 *  icon on a workspace row. These tests pin: the list offers "+ Start fresh…", no row carries the ↻
 *  icon anymore, and the item still drives the exact same flow (confirm → swap to the seed slot with
 *  fresh=true) as the old icon did.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { WorkspaceSwitcher } from "../workspace";
import * as api from "../workspaceApi";

vi.mock("../workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../workspaceApi")>()),
  readAttachedWorkspaces: vi.fn(),
  readActiveSet: vi.fn(),
  swapWorkspace: vi.fn(),
  activateWorkspace: vi.fn(),
  deactivateWorkspace: vi.fn(),
}));

const view = {
  active: "leo",
  slots: {
    seed: { repo: null, ref: null, name: "default (previous)" },
    leo: { repo: null, ref: null, name: "leo" },
  },
} as unknown as Awaited<ReturnType<typeof api.readAttachedWorkspaces>>;

// The active set: `leo` is the PRIMARY (private baseline, always mounted); `seed` is parked (available).
const activeSet = {
  subject: "u1",
  active: [{ slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true }],
} as unknown as Awaited<ReturnType<typeof api.readActiveSet>>;

async function renderOpenSwitcher(overrideActive?: Awaited<ReturnType<typeof api.readActiveSet>>) {
  sessionStorage.setItem("ws.attach.open", "1"); // section open by default for the test
  vi.mocked(api.readAttachedWorkspaces).mockResolvedValue(view);
  vi.mocked(api.readActiveSet).mockResolvedValue(overrideActive ?? activeSet);
  vi.mocked(api.swapWorkspace).mockResolvedValue(undefined as never);
  vi.mocked(api.activateWorkspace).mockResolvedValue({ subject: "u1", slug: "seed", changed: true, cloned: false, nested: false });
  vi.mocked(api.deactivateWorkspace).mockResolvedValue({ subject: "u1", slug: "seed", changed: true });
  const onSwapped = vi.fn();
  render(<WorkspaceSwitcher onSwapped={onSwapped} />);
  await screen.findByText("leo"); // slots loaded
  return { onSwapped };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("WorkspaceSwitcher — start fresh is a list-level action", () => {
  it("offers '+ Start fresh…' as a list item alongside '+ Attach repo…'", async () => {
    await renderOpenSwitcher();
    expect(screen.getByText("Attach repo…")).toBeTruthy();
    expect(screen.getByText("Start fresh…")).toBeTruthy();
  });

  it("no workspace row carries the old ↻ icon anymore", async () => {
    await renderOpenSwitcher();
    expect(screen.queryByText("↻")).toBeNull();
  });

  it("clicking it (confirmed) runs the SAME flow as before: swap to the seed slot with fresh=true", async () => {
    const { onSwapped } = await renderOpenSwitcher();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByText("Start fresh…"));
    await waitFor(() => expect(api.swapWorkspace).toHaveBeenCalledWith(undefined, undefined, undefined, true, "seed"));
    expect(confirm).toHaveBeenCalledOnce();
    await waitFor(() => expect(onSwapped).toHaveBeenCalled());
    confirm.mockRestore();
  });

  it("declining the confirmation swaps nothing", async () => {
    await renderOpenSwitcher();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByText("Start fresh…"));
    expect(api.swapWorkspace).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

describe("WorkspaceSwitcher — additive active set (WP-A2.1)", () => {
  it("a parked workspace's toggle ACTIVATES it (adds to the mount set, no swap/park)", async () => {
    const { onSwapped } = await renderOpenSwitcher();
    // `seed` is parked (○) — clicking its label activates it WITHOUT parking `leo` (the additive path)
    fireEvent.click(screen.getByText("default (previous)"));
    await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith({ slug: "seed" }));
    expect(api.swapWorkspace).not.toHaveBeenCalled();  // additive, not swap-park
    await waitFor(() => expect(onSwapped).toHaveBeenCalled());
  });

  it("a mounted secondary's toggle DEACTIVATES it (parks — never destroyed)", async () => {
    // both leo (primary) and seed are mounted; seed's toggle should park seed
    await renderOpenSwitcher({
      subject: "u1",
      active: [
        { slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true },
        { slug: "seed", repo: null, ref: null, role: "private", path: "/w/.attached/u1/seed", write: true, primary: false },
      ],
    } as unknown as Awaited<ReturnType<typeof api.readActiveSet>>);
    fireEvent.click(screen.getByText("default (previous)"));
    await waitFor(() => expect(api.deactivateWorkspace).toHaveBeenCalledWith("seed"));
  });

  it("the PRIMARY baseline can't be toggled off (no activate/deactivate call)", async () => {
    await renderOpenSwitcher();
    fireEvent.click(screen.getByText("leo"));  // the primary
    // give any async a tick; the primary click is a no-op
    await new Promise((r) => setTimeout(r, 0));
    expect(api.deactivateWorkspace).not.toHaveBeenCalled();
    expect(api.activateWorkspace).not.toHaveBeenCalled();
  });

  it("'Attach repo…' ADDS the repo to the set (activate), not a swap", async () => {
    await renderOpenSwitcher();
    fireEvent.click(screen.getByText("Attach repo…"));
    fireEvent.change(screen.getByPlaceholderText("git repo URL"), { target: { value: "https://example.com/r.git" } });
    fireEvent.click(screen.getByText("Attach"));
    await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith({ repo: "https://example.com/r.git", ref: undefined, token: undefined }));
    expect(api.swapWorkspace).not.toHaveBeenCalled();
  });
});
