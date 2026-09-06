/** "EXTEND" AS THE READER PRESSES IT — through the pages panel, which is where both triggers live.
 *
 *  Rendered via `PagesPanel` rather than as loose components, because the two claims that matter
 *  are about the panel: that the intent is built from the RESOLVED VIEW SLOT and not from anything
 *  on screen (F63), and that pressing it does not mint a tab (decision 28). Neither is observable
 *  from a button in isolation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";

// THE RENDERED DOCUMENT AS ONE TEXT NODE. The selection tests need exact offsets, and they used to
// get them from the `</>` lens — which the founder removed on 2026-09-06. Mocking the renderer says
// the same thing more directly: these are about the ACTION, not about how MDX splits a paragraph.
vi.mock("../../ui-kit/MdxDoc", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  const { createElement } = await import("react");
  return { ...orig, MdxDoc: (p: { children?: unknown }) => createElement("div", { "data-mdx": "" }, p.children as never) };
});

import { PagesPanel } from "../PagesPanel";
import { ASK_CHAT_EVENT, WORKSPACE_COMMIT_EVENT } from "../../canvas/actions";
import { VIEW_NAVIGATE_EVENT } from "../roomView";
import { clearPending } from "../extend";
import type { PageIntent } from "../../surfaces/chatIntent";
import type { Page } from "../types";

const PATH = "kg/entities/company/helm.md";
const BODY = "# Helm Bank\n\nThe pilot ships in March, self-hosted.\n";
// the tab's LABEL is deliberately not the path, and deliberately misleading: an intent built from
// what is written on a chip would pick this up
const pages: Page[] = [{ path: PATH, slug: "acme-kg", label: "Some Other Name" }];

// this suite is about PAGE intents (extend/create); narrowing here is the assertion, not a cast
const asks: { prompt?: string; display?: string; intent?: PageIntent }[] = [];
const views: unknown[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const onView = (e: Event) => views.push((e as CustomEvent).detail);
const onOpen = vi.fn();

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={pages} docPath={PATH} docSlug="acme-kg" onOpen={onOpen} body={BODY} {...over} />);

beforeEach(() => {
  asks.length = 0; views.length = 0; onOpen.mockClear(); clearPending();
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
  window.addEventListener(VIEW_NAVIGATE_EVENT, onView);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  window.removeEventListener(VIEW_NAVIGATE_EVENT, onView);
  cleanup();
});

describe("the page action — the open page, whole (decision 32.1)", () => {
  it("posts the intent for the RESOLVED slot, not for what the tab is called (F63)", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);

    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "extend", workspace: "acme-kg", path: PATH });
    expect(JSON.stringify(asks[0])).not.toContain("Some Other Name");
  });

  it("the bubble is the compact form — the prompt is never what the reader is shown as their words", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    expect(asks[0].display).toBe(`Extend: ${PATH}`);
    expect(asks[0].display).not.toMatch(/follow the links|research|write back/i);
  });

  it("the desk's own page carries NO workspace — an absent slug is an answer", () => {
    const { container } = panel({ docSlug: undefined, docPath: "README.md", pages: [{ path: "README.md", label: "README" }] });
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    expect(asks[0].intent).toEqual({ kind: "extend", path: "README.md" });
  });

  it("mints no tab (decision 28)", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("a meeting canvas has no page to extend, so it has no action", () => {
    const { container } = render(
      <PagesPanel pages={[{ kind: "meeting", path: "42", label: "Standup" }]} docPath="42" docKind="meeting" onOpen={onOpen} body={null} />,
    );
    expect(container.querySelector('[data-doc-act="extend"]')).toBeNull();
  });
});

describe("the empty state's action (decision 32.4)", () => {
  it("offers to create the page that is not there", () => {
    const { container } = panel({ body: null });
    expect(screen.getByText(/No page here yet/)).toBeTruthy();
    fireEvent.click(container.querySelector('[data-doc-act="create"]') as HTMLElement);

    expect(asks[0].intent).toEqual({ kind: "create", workspace: "acme-kg", path: PATH });
    expect(asks[0].display).toBe(`Create: ${PATH}`);
    expect(asks[0].prompt).toBe(`Create: ${PATH}`);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("a page that HAS content is not offered a create button", () => {
    const { container } = panel();
    expect(container.querySelector('[data-doc-act="create"]')).toBeNull();
  });
});

/** Select `length` characters of the first text node inside `host`, starting at `from`. */
function highlight(host: HTMLElement, from: number, length: number) {
  const node = host.firstChild as Text;
  const range = document.createRange();
  range.setStart(node, from);
  range.setEnd(node, from + length);
  const sel = window.getSelection() as Selection;
  sel.removeAllRanges();
  sel.addRange(range);
  act(() => { document.dispatchEvent(new Event("selectionchange")); });
}

describe("the floating action on a selection (decision 32.1)", () => {
  /** the rendered document — one text node, thanks to the mock at the top of this file */
  const rendered = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
    panel(over).container.querySelector("[data-mdx]") as HTMLElement;

  it("appears only once there is a selection, and carries its text", () => {
    const pre = rendered();
    expect(document.querySelector('[data-doc-act="extend-selection"]')).toBeNull();

    highlight(pre, 13, 24);            // "The pilot ships in March"
    const btn = document.querySelector('[data-doc-act="extend-selection"]') as HTMLElement;
    expect(btn).toBeTruthy();

    fireEvent.mouseDown(btn);
    expect(asks[0].intent).toMatchObject({ kind: "extend", workspace: "acme-kg", path: PATH, selection: "The pilot ships in March" });
    expect(asks[0].display).toBe(`Extend: ${PATH} — “The pilot ships in March”`);
    expect(asks[0].prompt).toBe(`Extend: ${PATH} — 'The pilot ships in March'`);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("locates the selection in the SOURCE when it occurs there exactly once", () => {
    const pre = rendered();
    highlight(pre, 13, 24);
    fireEvent.mouseDown(document.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    const r = asks[0].intent?.selection_range;
    expect(r).toBeTruthy();
    expect(BODY.slice(r!.start, r!.end)).toBe("The pilot ships in March");
  });

  it("says nothing about where a selection sits when the text repeats", () => {
    const body = "one two\n\none two\n";
    const pre = rendered({ body });
    highlight(pre, 0, 7);              // "one two", twice in the file
    fireEvent.mouseDown(document.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    expect(asks[0].intent?.selection).toBe("one two");
    expect(asks[0].intent?.selection_range).toBeUndefined();
  });

  it("caps a very long selection at 2000 characters", () => {
    const body = "y".repeat(4000);
    const pre = rendered({ body });
    highlight(pre, 0, 4000);
    fireEvent.mouseDown(document.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    expect(asks[0].intent?.selection).toHaveLength(2000);
  });

  it("a selection OUTSIDE the document is not this page's selection", () => {
    rendered();
    const stray = document.createElement("p");
    stray.textContent = "text in some other pane";
    document.body.appendChild(stray);
    highlight(stray, 0, 9);
    expect(document.querySelector('[data-doc-act="extend-selection"]')).toBeNull();
    stray.remove();
  });

  it("an editor's selection is being edited, not asked about", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="edit"]') as HTMLElement);
    expect(container.querySelector('[data-doc-act="extend-selection"]')).toBeNull();
  });
});

describe("the landing (decision 32.3)", () => {
  it("the page becomes the view when the turn commits — and no tab is minted", () => {
    const { container } = panel({ body: null });
    fireEvent.click(container.querySelector('[data-doc-act="create"]') as HTMLElement);
    expect(views).toEqual([]);                       // not before the reply

    act(() => { window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT)); });
    expect(views).toEqual([{ workspace: "acme-kg", path: PATH, label: "helm" }]);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("a commit with nothing pending navigates nowhere", () => {
    panel();
    act(() => { window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT)); });
    expect(views).toEqual([]);
  });
});
