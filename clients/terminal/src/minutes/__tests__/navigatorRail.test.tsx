/** THE RAIL, AS THE READER MEETS IT — inside the pages panel, through the panel's own props.
 *
 *  Rendered via `PagesPanel` on purpose. The rail is not a component someone drops on a page: it is
 *  a column of that panel, its toggle is that panel's leftmost control, and the one thing a click
 *  must never do — mint a tab — is only observable where the panel's tab route is. Testing the
 *  Navigator alone would prove the rail draws and prove nothing about the seam.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import { NAV_OPEN_KEY } from "../navigatorApi";
import { VIEW_NAVIGATE_EVENT } from "../roomView";
import type { Page } from "../types";
import * as nav from "../navigatorApi";

vi.mock("../navigatorApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../navigatorApi")>()),
  loadNavWorkspaces: vi.fn(),
  loadNavTree: vi.fn(),
}));

const WORKSPACES: nav.NavWorkspace[] = [
  { key: "desk", slug: undefined, name: "Dmitry's desk", kind: "desk" },
  { key: "_global", slug: "_global", name: "Helm Bank", kind: "global" },
  { key: "acme-kg", slug: "acme-kg", name: "Acme", kind: "group" },
];
const TREES: Record<string, string[]> = {
  desk: ["README.md", "drafts/brief.md", "CLAUDE.md", "flows/personal.md", ".scaffolded"],
  _global: ["PRINCIPLES.md"],
  "acme-kg": ["kg/brief-2026.md"],
};

const PATH = "README.md";
const pages: Page[] = [{ path: PATH, label: "README" }];
const onOpen = vi.fn();

const panel = () => render(<PagesPanel pages={pages} docPath={PATH} onOpen={onOpen} body={"# Desk"} />);
const toggle = (c: HTMLElement) => c.querySelector("[data-nav-toggle]") as HTMLElement;

beforeEach(() => {
  localStorage.clear();
  onOpen.mockClear();
  vi.mocked(nav.loadNavWorkspaces).mockReset().mockResolvedValue(WORKSPACES);
  vi.mocked(nav.loadNavTree).mockReset().mockImplementation(async (ws: nav.NavWorkspace) => TREES[ws.key] ?? []);
});
afterEach(cleanup);

/** open the rail and wait for the workspace rows to land */
async function openRail() {
  const { container } = panel();
  fireEvent.click(toggle(container));
  await screen.findByText("Dmitry's desk");
  return container;
}

/** A rail row, waited for. Never `findByText` for a filename: the panel's own doc header prints
 *  the open document's name too, so "README.md" is genuinely ambiguous on this screen — and a test
 *  that matches the header instead of the row would pass with the rail rendering nothing. */
const rowOf = (c: HTMLElement, key: string) => c.querySelector(`[data-nav-file="${key}"]`) as HTMLElement | null;
const waitRow = async (c: HTMLElement, key: string) => {
  await waitFor(() => expect(rowOf(c, key)).toBeTruthy());
  return rowOf(c, key) as HTMLElement;
};

describe("default hidden, one toggle, remembered (decision 27.4)", () => {
  it("is not there until it is asked for", () => {
    const { container } = panel();
    expect(container.querySelector("[data-navigator]")).toBeNull();
    expect(toggle(container)).toBeTruthy();
    expect(toggle(container).getAttribute("aria-pressed")).toBe("false");
  });

  it("the toggle opens it, and the choice survives a remount", async () => {
    const container = await openRail();
    expect(container.querySelector("[data-navigator]")).toBeTruthy();
    expect(localStorage.getItem(NAV_OPEN_KEY)).toBe("1");

    cleanup();
    const again = panel();
    expect(again.container.querySelector("[data-navigator]")).toBeTruthy();   // remembered

    fireEvent.click(toggle(again.container));
    expect(again.container.querySelector("[data-navigator]")).toBeNull();
    expect(localStorage.getItem(NAV_OPEN_KEY)).toBe("0");
  });

  it("nothing is fetched while it is closed", () => {
    panel();
    expect(nav.loadNavWorkspaces).not.toHaveBeenCalled();
  });
});

describe("the workspaces (decisions 26–27.1)", () => {
  it("lists them in registry order, desk first", async () => {
    const container = await openRail();
    const rows = [...container.querySelectorAll("[data-nav-ws]")].map((b) => b.getAttribute("data-nav-ws"));
    expect(rows).toEqual(["desk", "_global", "acme-kg"]);
    expect(screen.getByText("Helm Bank")).toBeTruthy();      // `_global` under the company's name
  });

  it("every row on the rail opens — no greyed row, no `not available to you` (founder, 2026-09-06)", async () => {
    const container = await openRail();
    expect(screen.queryByText("not available to you")).toBeNull();
    const rows = [...container.querySelectorAll("[data-nav-ws]")] as HTMLButtonElement[];
    expect(rows.length).toBe(WORKSPACES.length);
    for (const row of rows) {
      expect(row.disabled, row.textContent ?? "").toBe(false);
      expect(row.getAttribute("aria-expanded"), row.textContent ?? "").toBe("false");
    }
  });

  it("expands lazily — a tree is read when it is asked for, and once", async () => {
    const container = await openRail();
    expect(nav.loadNavTree).not.toHaveBeenCalled();

    fireEvent.click(container.querySelector('[data-nav-ws="desk"]') as HTMLElement);
    await waitRow(container, "desk|README.md");
    expect(nav.loadNavTree).toHaveBeenCalledTimes(1);

    fireEvent.click(container.querySelector('[data-nav-ws="desk"]') as HTMLElement);   // collapse
    expect(rowOf(container, "desk|README.md")).toBeNull();
    fireEvent.click(container.querySelector('[data-nav-ws="desk"]') as HTMLElement);   // and back
    await waitRow(container, "desk|README.md");
    expect(nav.loadNavTree).toHaveBeenCalledTimes(1);
  });

  it("shows human files only — no machinery, no dotfiles (decision 27.2)", async () => {
    const container = await openRail();
    fireEvent.click(container.querySelector('[data-nav-ws="desk"]') as HTMLElement);
    await waitRow(container, "desk|README.md");

    expect(screen.getByText("drafts")).toBeTruthy();
    expect(screen.queryByText("CLAUDE.md")).toBeNull();
    expect(screen.queryByText("flows")).toBeNull();
    expect(screen.queryByText(".scaffolded")).toBeNull();
    expect(container.querySelector('[data-nav-file="desk|CLAUDE.md"]')).toBeNull();
  });
});

describe("opening a file (decision 28)", () => {
  const seen: unknown[] = [];
  const spy = (e: Event) => seen.push((e as CustomEvent).detail);
  beforeEach(() => { seen.length = 0; window.addEventListener(VIEW_NAVIGATE_EVENT, spy); });
  afterEach(() => window.removeEventListener(VIEW_NAVIGATE_EVENT, spy));

  async function deskOpen() {
    const container = await openRail();
    fireEvent.click(container.querySelector('[data-nav-ws="desk"]') as HTMLElement);
    await waitRow(container, "desk|README.md");
    return container;
  }

  it("a click NAVIGATES the view slot and mints no tab", async () => {
    const container = await deskOpen();
    fireEvent.click(container.querySelector('[data-nav-file="desk|README.md"]') as HTMLElement);
    expect(seen).toEqual([{ workspace: undefined, path: "README.md", label: "README" }]);
    expect(onOpen).not.toHaveBeenCalled();          // the tab route is untouched
  });

  it("the destination carries the workspace it came from — through the folders it lives in", async () => {
    const container = await openRail();
    fireEvent.click(container.querySelector('[data-nav-ws="acme-kg"]') as HTMLElement);
    // the tree NESTS: the file is inside `kg/`, and the folder opens before the file exists on screen
    await waitFor(() => expect(container.querySelector('[data-nav-dir="acme-kg|kg"]')).toBeTruthy());
    expect(rowOf(container, "acme-kg|kg/brief-2026.md")).toBeNull();
    fireEvent.click(container.querySelector('[data-nav-dir="acme-kg|kg"]') as HTMLElement);

    fireEvent.click(await waitRow(container, "acme-kg|kg/brief-2026.md"));
    expect(seen).toEqual([{ workspace: "acme-kg", path: "kg/brief-2026.md", label: "brief-2026" }]);
  });

  it("a TAB is minted only when the reader says so — and through the panel's own route", async () => {
    const container = await deskOpen();
    fireEvent.click(container.querySelector('[data-nav-tab="desk|README.md"]') as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith({ path: "README.md", slug: undefined, label: "README" });
    expect(seen).toEqual([]);                       // and it did not ALSO navigate
  });

  it("middle-click is the same explicit act", async () => {
    const container = await deskOpen();
    // no `fireEvent.auxClick` in this DOM-testing-library — so the event itself, which is what the
    // browser sends anyway
    fireEvent(rowOf(container, "desk|README.md") as HTMLElement,
      new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(onOpen).toHaveBeenCalledWith({ path: "README.md", slug: undefined, label: "README" });
  });
});

describe("the filter (decision 27.3)", () => {
  it("searches every listed workspace and groups the hits by workspace", async () => {
    const container = await openRail();
    const box = container.querySelector("[data-nav-filter]") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "brief" } });

    await waitFor(() => expect(container.querySelectorAll("[data-nav-group]").length).toBe(2));
    const groups = [...container.querySelectorAll("[data-nav-group]")].map((g) => g.getAttribute("data-nav-group"));
    expect(groups).toEqual(["desk", "acme-kg"]);    // list order; `_global` has no hit
    expect(screen.getByText("drafts/brief.md")).toBeTruthy();
    expect(screen.getByText("kg/brief-2026.md")).toBeTruthy();
  });

  it("`>` says out loud that content search is not on this build, and still matches names", async () => {
    const container = await openRail();
    fireEvent.change(container.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "> brief" } });
    await waitFor(() => expect(container.querySelector("[data-nav-note]")).toBeTruthy());
    expect(container.querySelector("[data-nav-note]")?.textContent).toContain("not available yet");
    expect(container.querySelectorAll("[data-nav-group]").length).toBe(2);
  });

  it("a filter that matches nothing says nothing matched", async () => {
    const container = await openRail();
    fireEvent.change(container.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "zzz" } });
    await waitFor(() => expect(container.querySelector("[data-nav-empty]")).toBeTruthy());
  });
});

describe("the keyboard", () => {
  it("`/` focuses the filter while the rail is open", async () => {
    const container = await openRail();
    const box = container.querySelector("[data-nav-filter]") as HTMLInputElement;
    expect(document.activeElement).not.toBe(box);
    act(() => { fireEvent.keyDown(document.body, { key: "/" }); });
    expect(document.activeElement).toBe(box);
  });

  it("`/` typed INTO a field is a character, not a shortcut", async () => {
    const container = await openRail();
    const box = container.querySelector("[data-nav-filter]") as HTMLInputElement;
    const other = document.createElement("input");
    document.body.appendChild(other);
    other.focus();
    act(() => { fireEvent.keyDown(other, { key: "/" }); });
    expect(document.activeElement).toBe(other);
    expect(document.activeElement).not.toBe(box);
    other.remove();
  });

  it("Escape closes the rail", async () => {
    const container = await openRail();
    act(() => { fireEvent.keyDown(document.body, { key: "Escape" }); });
    await waitFor(() => expect(container.querySelector("[data-navigator]")).toBeNull());
    expect(localStorage.getItem(NAV_OPEN_KEY)).toBe("0");
  });
});

describe("the folder listing reads the same hide list", () => {
  it("machinery the breadcrumb walked into is not offered either", () => {
    const { container } = render(
      <PagesPanel pages={pages} docPath={PATH} onOpen={onOpen} body={"# Desk"}
        listing={{ prefix: "", dirs: ["drafts", "flows", "skills"], files: ["README.md", "CLAUDE.md"] }} />,
    );
    const names = [...container.querySelectorAll("[data-entry]")].map((b) => b.textContent);
    expect(names).toEqual(["drafts/", "README.md"]);
  });
});
