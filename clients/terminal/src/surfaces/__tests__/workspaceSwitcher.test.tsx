/** Behavioral test for the WORKSPACES sidebar section (workspace.tsx WorkspaceSwitcher).
 *
 *  ADDITIVE model (WP-A1): the list-level "+ New workspace…" action CREATES a fresh blank workspace and
 *  ADDS it to the mount set (a new CHECKED row) — it does NOT swap/rebuild/park the baseline, and pops NO
 *  scary confirm (creating a workspace is non-destructive). It lives alongside "+ Attach repo…" as the two
 *  ways to bring a workspace into the set. These tests pin: the list offers "+ New workspace…" (not the old
 *  "Start fresh…"), clicking it calls createWorkspace (never swapWorkspace/fresh) with no window.confirm,
 *  and no row carries the old ↻ icon.
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
  createWorkspace: vi.fn(),
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
  vi.mocked(api.createWorkspace).mockResolvedValue({ subject: "u1", slug: "workspace-1", changed: true, added: true });
  const onSwapped = vi.fn();
  render(<WorkspaceSwitcher onSwapped={onSwapped} />);
  await screen.findByText("leo"); // slots loaded
  return { onSwapped };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("WorkspaceSwitcher — '+ New workspace…' CREATES + ADDS (additive), not swap-rebuild-baseline", () => {
  it("offers '+ New workspace…' as a list item alongside '+ Attach repo…' (not the old 'Start fresh…')", async () => {
    await renderOpenSwitcher();
    expect(screen.getByText("Attach repo…")).toBeTruthy();
    expect(screen.getByText("New workspace…")).toBeTruthy();
    expect(screen.queryByText("Start fresh…")).toBeNull();  // the old swap-rebuild action is gone
  });

  it("no workspace row carries the old ↻ icon anymore", async () => {
    await renderOpenSwitcher();
    expect(screen.queryByText("↻")).toBeNull();
  });

  it("clicking it calls createWorkspace — NOT swapWorkspace/fresh — and pops NO confirm", async () => {
    const { onSwapped } = await renderOpenSwitcher();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByText("New workspace…"));
    await waitFor(() => expect(api.createWorkspace).toHaveBeenCalled());
    expect(confirm).not.toHaveBeenCalled();                 // creating a workspace is non-destructive
    expect(api.swapWorkspace).not.toHaveBeenCalled();       // NOT the swap-rebuild-baseline path
    await waitFor(() => expect(onSwapped).toHaveBeenCalled());
    confirm.mockRestore();
  });

  it("re-reads the active set after creating, so the new (checked) row shows up", async () => {
    await renderOpenSwitcher();
    vi.mocked(api.readActiveSet).mockClear();
    fireEvent.click(screen.getByText("New workspace…"));
    // the reload() after create re-fetches the active set → the new checked row is picked up
    await waitFor(() => expect(api.readActiveSet).toHaveBeenCalled());
  });

  it("the new workspace loads back as a CHECKED (active) row, existing rows unaffected", async () => {
    // after create, the backend adds workspace-1 to the active set; the switcher reloads and renders it
    // as a checked row alongside the untouched baseline (leo) and parked seed.
    await renderOpenSwitcher();
    vi.mocked(api.readAttachedWorkspaces).mockResolvedValue({
      active: "leo",
      slots: {
        seed: { repo: null, ref: null, name: "default (previous)" },
        leo: { repo: null, ref: null, name: "leo" },
        "workspace-1": { repo: null, ref: null, name: "New workspace" },
      },
    } as unknown as Awaited<ReturnType<typeof api.readAttachedWorkspaces>>);
    vi.mocked(api.readActiveSet).mockResolvedValue({
      subject: "u1",
      active: [
        { slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true },
        { slug: "workspace-1", repo: null, ref: null, role: "private", path: "/w/.attached/u1/workspace-1", write: true, primary: false },
      ],
    } as unknown as Awaited<ReturnType<typeof api.readActiveSet>>);
    fireEvent.click(screen.getByText("New workspace…"));
    const newRow = await screen.findByText("New workspace");
    const cb = newRow.closest("div")!.querySelector<HTMLElement>('[role="checkbox"]')!;
    await waitFor(() => expect(cb.getAttribute("aria-checked")).toBe("true"));  // new row is CHECKED
    // the baseline (leo) is still checked+disabled — untouched by the create
    const leoCb = screen.getByText("leo").closest("div")!.querySelector<HTMLElement>('[role="checkbox"]')!;
    expect(leoCb.getAttribute("aria-checked")).toBe("true");
    expect(leoCb.getAttribute("aria-disabled")).toBe("true");
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

describe("WorkspaceSwitcher — active set uses CHECKBOXES, not radio-style dots (multi-active is the model)", () => {
  // The multi-select mental model must be visually obvious: each row renders a real role="checkbox"
  // (checked = mounted, unchecked = parked). A filled/hollow dot read as a single-select radio.
  const rowCheckbox = (name: string) =>
    screen.getByText(name).closest("div")!.querySelector<HTMLElement>('[role="checkbox"]')!;

  it("each workspace row renders a real accessible checkbox (no ●/○ dot)", async () => {
    await renderOpenSwitcher();
    // one checkbox per slot row (seed + leo)
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThanOrEqual(2);
    // the old dot affordance is gone
    expect(screen.queryByText("●")).toBeNull();
    expect(screen.queryByText("○")).toBeNull();
  });

  it("the checkbox reflects active-set membership: primary checked, parked unchecked", async () => {
    await renderOpenSwitcher();  // leo = primary (mounted), seed = parked
    expect(rowCheckbox("leo").getAttribute("aria-checked")).toBe("true");
    expect(rowCheckbox("default (previous)").getAttribute("aria-checked")).toBe("false");
  });

  it("clicking a parked row's checkbox toggles via activate (adds to the mount set)", async () => {
    await renderOpenSwitcher();
    fireEvent.click(rowCheckbox("default (previous)"));  // seed is parked → activate
    await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith({ slug: "seed" }));
  });

  it("clicking a mounted secondary's checkbox toggles via deactivate (parks it)", async () => {
    await renderOpenSwitcher({
      subject: "u1",
      active: [
        { slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true },
        { slug: "seed", repo: null, ref: null, role: "private", path: "/w/.attached/u1/seed", write: true, primary: false },
      ],
    } as unknown as Awaited<ReturnType<typeof api.readActiveSet>>);
    fireEvent.click(rowCheckbox("default (previous)"));  // seed is mounted → deactivate
    await waitFor(() => expect(api.deactivateWorkspace).toHaveBeenCalledWith("seed"));
  });

  it("the private baseline's checkbox is CHECKED and DISABLED (can't be unchecked)", async () => {
    await renderOpenSwitcher();
    const cb = rowCheckbox("leo");  // the primary baseline
    expect(cb.getAttribute("aria-checked")).toBe("true");
    expect(cb.getAttribute("aria-disabled")).toBe("true");
    expect(cb.getAttribute("title")).toContain("always active");
    // clicking it is a no-op (no activate/deactivate)
    fireEvent.click(cb);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.deactivateWorkspace).not.toHaveBeenCalled();
    expect(api.activateWorkspace).not.toHaveBeenCalled();
  });

  it("MULTIPLE rows can be checked at once (the UI reflects >1 mounted — additive, not radio)", async () => {
    await renderOpenSwitcher({
      subject: "u1",
      active: [
        { slug: "leo", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true },
        { slug: "seed", repo: null, ref: null, role: "private", path: "/w/.attached/u1/seed", write: true, primary: false },
      ],
    } as unknown as Awaited<ReturnType<typeof api.readActiveSet>>);
    const checked = screen.getAllByRole("checkbox").filter((c) => c.getAttribute("aria-checked") === "true");
    expect(checked.length).toBe(2);  // both leo AND seed checked simultaneously
  });

  it("the checkbox is keyboard-operable (Space toggles via activate)", async () => {
    await renderOpenSwitcher();
    const cb = rowCheckbox("default (previous)");
    expect(cb.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(cb, { key: " " });
    await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith({ slug: "seed" }));
  });
});
