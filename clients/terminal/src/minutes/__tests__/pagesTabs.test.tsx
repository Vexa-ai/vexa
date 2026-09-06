/** THE PAGES PANEL AFTER THE 2026-09-06 WALK — what the four rulings look like on screen.
 *
 *  The strip's arithmetic (a navigation replaces the preview, a pin promotes, a scaffold's tabs
 *  survive) is pinned where it lives, in `stripHistory.test.ts` and `scaffold.test.ts`. This file
 *  is the other half: that the panel RENDERS the model — the pin control on the tab rather than in
 *  the document header, the preview legible as a preview, no `</>`, Extend under the content as
 *  a labelled control that still fires the same act — and no `×` on the home, the one tab the
 *  model refuses to drop.
 *
 *  Each has a wrong answer that photographs well. A pin in the header looks like a pin; it just
 *  cannot say which tab it is about. Extend as the sixth 14px glyph looks like a full icon row.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";

// one text node for the document, so "under the content" is a position this test can read
vi.mock("../../ui-kit/MdxDoc", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  const { createElement } = await import("react");
  return { ...orig, MdxDoc: (p: { children?: unknown }) => createElement("div", { "data-mdx": "" }, p.children as never) };
});

import { PagesPanel } from "../PagesPanel";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import type { PageIntent } from "../../surfaces/chatIntent";
import type { Page } from "../types";

const PATH = "kg/entities/company/academy-software-foundation.md";
const BODY = "# ASWF\n\nThe foundation runs the DNA project.\n";
/** the founder's screenshot, as a strip: the chat's home, a tab he kept, and the page he is on */
const STRIP: Page[] = [
  { path: "README.md", label: "Desk", desk: true },
  { path: "_global/PRINCIPLES.md", slug: "_global", label: "PRINCIPLES", pinned: true },
  { path: PATH, slug: "_global", label: "academy-software-foundation", at: 3 },
];

const onTogglePin = vi.fn();
const onOpen = vi.fn();
const asks: { intent?: PageIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={STRIP} docPath={PATH} docSlug="_global" onOpen={onOpen}
    onTogglePin={onTogglePin} onClose={() => {}} body={BODY} {...over} />);

beforeEach(() => { onTogglePin.mockClear(); onOpen.mockClear(); asks.length = 0; window.addEventListener(ASK_CHAT_EVENT, onAsk); });
afterEach(() => { window.removeEventListener(ASK_CHAT_EVENT, onAsk); cleanup(); vi.restoreAllMocks(); });

describe('the pin is ON the tab (founder: "tab icon is on tab")', () => {
  it("gives every tab its own, naming the page it is about", () => {
    const { container } = panel();
    const pins = [...container.querySelectorAll("[data-tab-pin]")];
    // one per tab EXCEPT the home: that is a product default, not something the reader asked for,
    // so there is no decision to offer on it
    expect(pins.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Unpin PRINCIPLES", "Keep academy-software-foundation as a tab",
    ]);
  });

  it("presses back with THAT page — not with whatever happens to be in front", () => {
    const { container } = panel();
    fireEvent.click(container.querySelectorAll("[data-tab-pin]")[0]);
    expect(onTogglePin).toHaveBeenCalledWith(STRIP[1]);   // the pinned tab, while the doc is STRIP[2]
    expect(onOpen).not.toHaveBeenCalled();                // pinning is not opening
  });

  it("says which tabs are KEPT and which one is the preview", () => {
    const { container } = panel();
    const tabs = [...container.querySelectorAll("[data-tab]")];
    expect(tabs.map((t) => t.hasAttribute("data-kept"))).toEqual([true, true, false]);
    // the preview reads as one, the way Obsidian's does — it is about to be replaced
    expect((tabs[2] as HTMLElement).style.fontStyle).toBe("italic");
    expect((tabs[1] as HTMLElement).style.fontStyle).toBe("");
  });

  it("is not in the document header any more", () => {
    const { container } = panel();
    expect(container.querySelector('[data-doc-act="pin"]')).toBeNull();
  });

  it("renders no pin control at all when the shell offers no handler", () => {
    const { container } = panel({ onTogglePin: undefined });
    expect(container.querySelector("[data-tab-pin]")).toBeNull();
  });
});

describe("the `×` is the reader's too — and the chat's home is not the reader's", () => {
  // `forgetHistory` has always refused the desk entry (`stripHistory.test.ts`: "× drops a tab,
  // except the home"), so a `×` on it was a control that did nothing when pressed — the defect
  // Vexa-ai/vexa#1600 removed for the meeting's own tabs, one tab to the left of them.
  const closes = (container: HTMLElement) =>
    [...container.querySelectorAll("[data-tab-close]")].map((b) => b.getAttribute("aria-label"));

  it("renders no close control on the desk tab, and one on every tab the reader put there", () => {
    const { container } = panel();
    expect(closes(container)).not.toContain("Close Desk");
    // "nothing closes" is the over-shoot: what the reader kept the reader may drop
    expect(closes(container)).toEqual(["Close PRINCIPLES", "Close academy-software-foundation"]);
  });

  it("an ordinary tab's `×` still closes THAT tab", () => {
    const onClose = vi.fn();
    const { container } = panel({ onClose });
    fireEvent.click([...container.querySelectorAll("[data-tab-close]")]
      .find((b) => b.getAttribute("aria-label") === "Close PRINCIPLES")!);
    expect(onClose).toHaveBeenCalledWith(STRIP[1]);
  });
});

describe('Extend, under the content (founder: "noticeable as one click knowledge expansion")', () => {
  it("sits under the document, not in the header's icon group", () => {
    const { container } = panel();
    const ext = container.querySelector('[data-doc-act="extend"]') as HTMLElement;
    const body = container.querySelector("[data-doc-body]") as HTMLElement;
    const header = container.querySelector("[data-doc-name]")!.parentElement as HTMLElement;

    expect(body.contains(ext)).toBe(true);
    expect(header.contains(ext)).toBe(false);
    // …and AFTER the page it is about, which is the whole of "under content"
    const doc = container.querySelector("[data-mdx]") as HTMLElement;
    expect(doc.compareDocumentPosition(ext) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says what it does, so nobody has to press it to find out", () => {
    const { container } = panel();
    const ext = container.querySelector('[data-doc-act="extend"]') as HTMLElement;
    expect(ext.textContent).toContain("Extend this page");
    expect(ext.textContent).toMatch(/research/i);
    expect(ext.textContent).toMatch(/link both ways/i);
  });

  it("fires the SAME act, for the resolved view slot", () => {
    const { container } = panel();
    // A press opens the optional one-line field first (Vexa-ai/vexa#1593); Escape fires the act
    // with no line, which is the payload this test has always asserted.
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    fireEvent.keyDown(container.querySelector("[data-act-field]") as HTMLElement, { key: "Escape" });
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "extend", workspace: "_global", path: PATH });
    expect(onOpen).not.toHaveBeenCalled();               // it navigates on commit, it mints no tab
  });

  it("a page that does not exist yet is offered Create instead — there is nothing to extend", () => {
    const { container } = panel({ body: null });
    expect(container.querySelector('[data-doc-act="extend"]')).toBeNull();
    expect(container.querySelector('[data-doc-act="create"]')).toBeTruthy();
  });

  it("an editor is not a reader — Extend stands down while the document is being written", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="edit"]') as HTMLElement);
    expect(container.querySelector('[data-doc-act="extend"]')).toBeNull();
  });
});
