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
  swapWorkspace: vi.fn(),
}));

const view = {
  active: "leo",
  slots: {
    seed: { repo: null, ref: null, name: "default (previous)" },
    leo: { repo: null, ref: null, name: "leo" },
  },
} as unknown as Awaited<ReturnType<typeof api.readAttachedWorkspaces>>;

async function renderOpenSwitcher() {
  sessionStorage.setItem("ws.attach.open", "1"); // section open by default for the test
  vi.mocked(api.readAttachedWorkspaces).mockResolvedValue(view);
  vi.mocked(api.swapWorkspace).mockResolvedValue(undefined as never);
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
