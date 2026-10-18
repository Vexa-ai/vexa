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
import { WORKSPACE_COMMIT_EVENT } from "../../platform";
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
/** A workspace row's CHEVRON — the plain expand/collapse. The row's NAME opens the front page. */
const caretOf = (c: HTMLElement, key: string) => c.querySelector(`[data-nav-ws-caret="${key}"]`) as HTMLElement;
const wsOf = (c: HTMLElement, key: string) => c.querySelector(`[data-nav-ws="${key}"]`) as HTMLElement;
const nameOf = (c: HTMLElement, key: string) => wsOf(c, key).querySelector("span:not([data-nav-ws-caret])") as HTMLElement;
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

  it("one panel at a time: with a meeting in front the files fold away; the toggle swaps them in, for that meeting only", async () => {
    localStorage.setItem(NAV_OPEN_KEY, "1");
    const { container } = render(<PagesPanel pages={pages} docPath="9121" docKind="meeting" onOpen={onOpen} body={null} />);
    expect(container.querySelector("[data-navigator]")).toBeNull();
    fireEvent.click(toggle(container));
    expect(container.querySelector("[data-navigator]")).toBeTruthy();
    expect((container.querySelector(".vx-pages-col") as HTMLElement).hidden).toBe(true);
    expect(localStorage.getItem(NAV_OPEN_KEY)).toBe("1");          // the document's choice untouched
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

    fireEvent.click(caretOf(container, "desk"));
    await waitRow(container, "desk|README.md");
    expect(nav.loadNavTree).toHaveBeenCalledTimes(1);

    fireEvent.click(caretOf(container, "desk"));   // collapse
    expect(rowOf(container, "desk|README.md")).toBeNull();
    fireEvent.click(caretOf(container, "desk"));   // and back
    await waitRow(container, "desk|README.md");
    expect(nav.loadNavTree).toHaveBeenCalledTimes(1);
  });

  it("shows human files only — no machinery, no dotfiles (decision 27.2)", async () => {
    const container = await openRail();
    fireEvent.click(caretOf(container, "desk"));
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
    fireEvent.click(caretOf(container, "desk"));
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
    fireEvent.click(caretOf(container, "acme-kg"));
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

describe("a workspace's NAME opens its front page (founder, 2026-10-11)", () => {
  /** *"click on workspace should open the readme"*. Before this, the whole row was a toggle: the
   *  workspace's own page was one expand and one more click away, and the click the reader made
   *  on the name did nothing but show a list. */
  const seen: unknown[] = [];
  const spy = (e: Event) => seen.push((e as CustomEvent).detail);
  beforeEach(() => { seen.length = 0; window.addEventListener(VIEW_NAVIGATE_EVENT, spy); });
  afterEach(() => window.removeEventListener(VIEW_NAVIGATE_EVENT, spy));

  it("clicking the name puts the README in front and expands the row", async () => {
    const container = await openRail();
    fireEvent.click(nameOf(container, "acme-kg"));
    expect(seen).toEqual([{ workspace: "acme-kg", path: "README.md", label: "README" }]);
    expect(onOpen).not.toHaveBeenCalled();                       // navigates, mints no tab
    await waitFor(() => expect(container.querySelector('[data-nav-dir="acme-kg|kg"]')).toBeTruthy());
    expect(wsOf(container, "acme-kg").getAttribute("aria-expanded")).toBe("true");
  });

  it("the desk and the company layer open the same way", async () => {
    const container = await openRail();
    fireEvent.click(nameOf(container, "desk"));
    fireEvent.click(nameOf(container, "_global"));
    expect(seen).toEqual([
      { workspace: undefined, path: "README.md", label: "README" },
      // `_global` here has no README in its tree: it opens as `/w/_global` does — never a blank pane
      { workspace: "_global", path: "README.md", label: "README" },
    ]);
  });

  it("a second click on the name opens it again and does NOT collapse the row", async () => {
    const container = await openRail();
    fireEvent.click(nameOf(container, "desk"));
    await waitRow(container, "desk|README.md");
    fireEvent.click(nameOf(container, "desk"));
    expect(seen).toHaveLength(2);
    expect(rowOf(container, "desk|README.md")).toBeTruthy();
  });

  it("the README is opened as the tree spells it", async () => {
    vi.mocked(nav.loadNavTree).mockImplementation(async (ws: nav.NavWorkspace) =>
      ws.key === "acme-kg" ? ["Readme.md", "kg/a.md"] : TREES[ws.key] ?? []);
    const container = await openRail();
    fireEvent.click(caretOf(container, "acme-kg"));
    await waitRow(container, "acme-kg|Readme.md");
    fireEvent.click(nameOf(container, "acme-kg"));
    expect(seen).toEqual([{ workspace: "acme-kg", path: "Readme.md", label: "Readme" }]);
  });

  it("the chevron alone only toggles — it opens nothing", async () => {
    const container = await openRail();
    fireEvent.click(caretOf(container, "desk"));
    await waitRow(container, "desk|README.md");
    fireEvent.click(caretOf(container, "desk"));
    expect(rowOf(container, "desk|README.md")).toBeNull();
    expect(seen).toEqual([]);
  });

  it("keyboard: Enter opens the front page, → expands, ← collapses", async () => {
    const container = await openRail();
    const row = wsOf(container, "desk");
    fireEvent.keyDown(row, { key: "ArrowRight" });
    await waitRow(container, "desk|README.md");
    expect(seen).toEqual([]);
    fireEvent.keyDown(row, { key: "ArrowLeft" });
    expect(rowOf(container, "desk|README.md")).toBeNull();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(seen).toEqual([{ workspace: undefined, path: "README.md", label: "README" }]);
    await waitRow(container, "desk|README.md");
  });
});

describe("a page that went away (Vexa-ai/vexa#1621)", () => {
  /** THE FIRST ACTS THIS RAIL HAS EVER HAD TO SURVIVE THAT REMOVE A FILE. `workspace_delete` and
   *  `workspace_move` take a page OUT of a workspace; every workspace's list is read exactly once,
   *  so without the commit listener the removed page keeps its row until the browser is reloaded —
   *  and clicking it opens "No page here yet", which reads as a broken rail rather than as a page
   *  that is gone. The document in front already re-reads on this event; this is the same fact
   *  reaching the other surface that shows the file. */
  it("re-reads an expanded workspace on a commit, so the deleted page loses its row", async () => {
    const container = await openRail();
    fireEvent.click(caretOf(container, "desk"));
    await waitRow(container, "desk|README.md");
    expect(nav.loadNavTree).toHaveBeenCalledTimes(1);

    // the turn removed the page and committed
    vi.mocked(nav.loadNavTree).mockImplementation(async (ws: nav.NavWorkspace) =>
      (TREES[ws.key] ?? []).filter((f) => f !== "README.md"));
    await act(async () => { window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT)); });

    await waitFor(() => expect(rowOf(container, "desk|README.md")).toBeNull());
    expect(nav.loadNavTree).toHaveBeenCalledTimes(2);
    expect(rowOf(container, "desk|drafts/brief.md") ?? screen.getByText("drafts")).toBeTruthy();
  });

  it("walks nothing nobody has opened — a commit is not a reason to read five workspaces", async () => {
    await openRail();
    await act(async () => { window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT)); });
    expect(nav.loadNavTree).not.toHaveBeenCalled();
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
