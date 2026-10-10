/** The 1d primitives: Splitter (keyboard + ARIA), Sheet (forms, focus, Esc), Menu (WAI-ARIA menu
 *  button), OverflowStrip (no silent hidden tabs), and the shell store (one key, tolerant reads). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Menu, OverflowStrip, Sheet, Splitter, splitterKey, readShellStore, writeShellStore, SHELL_KEY } from "..";

afterEach(cleanup);

describe("Splitter", () => {
  const b = { min: 200, max: 320, def: 240 };

  it("is a focusable separator with its value and range", () => {
    render(<Splitter label="Resize chat list" value={240} bounds={b} grows="right" onPreview={() => {}} onCommit={() => {}} />);
    const s = screen.getByRole("separator", { name: "Resize chat list" });
    expect(s.getAttribute("tabindex")).toBe("0");
    expect(s.getAttribute("aria-orientation")).toBe("vertical");
    expect(s.getAttribute("aria-valuenow")).toBe("240");
    expect(s.getAttribute("aria-valuemin")).toBe("200");
    expect(s.getAttribute("aria-valuemax")).toBe("320");
  });

  it("arrows move 16, Shift+arrows 64, Home/End jump, Enter resets — and the line follows the pointer", () => {
    expect(splitterKey("ArrowRight", false, 240, b, "right")).toBe(256);
    expect(splitterKey("ArrowLeft", false, 240, b, "right")).toBe(224);
    expect(splitterKey("ArrowRight", true, 240, b, "right")).toBe(304);
    // a right-hand pane grows as its line moves LEFT
    expect(splitterKey("ArrowLeft", false, 240, b, "left")).toBe(256);
    expect(splitterKey("Home", false, 240, b, "right")).toBe(200);
    expect(splitterKey("End", false, 240, b, "right")).toBe(320);
    expect(splitterKey("Enter", false, 240, b, "right")).toBeNull();
    expect(splitterKey("ArrowRight", true, 300, b, "right")).toBe(320);   // clamped
    expect(splitterKey("a", false, 240, b, "right")).toBeUndefined();
  });

  it("commits from the keyboard and resets on double-click", () => {
    const onCommit = vi.fn();
    render(<Splitter label="Resize" value={240} bounds={b} grows="right" onPreview={() => {}} onCommit={onCommit} />);
    const s = screen.getByRole("separator");
    fireEvent.keyDown(s, { key: "ArrowRight" });
    fireEvent.doubleClick(s);
    expect(onCommit.mock.calls).toEqual([[256], [null]]);
  });
});

describe("Sheet", () => {
  it("inline is display:contents and not a dialog", () => {
    render(<Sheet form="inline" open={false} onClose={() => {}} label="Pages"><button>inside</button></Sheet>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("inside")).toBeTruthy();
  });

  it("an open overlay is a modal dialog that takes focus and closes on Escape, returning focus", () => {
    const onClose = vi.fn();
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    const { rerender } = render(<Sheet form="overlay" open onClose={onClose} label="Pages" width={480}><button>first</button></Sheet>);
    const d = screen.getByRole("dialog", { name: "Pages" });
    expect(d.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(screen.getByText("first"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<Sheet form="overlay" open={false} onClose={onClose} label="Pages" width={480}><button>first</button></Sheet>);
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  it("a closed overlay stays mounted (state survives) but is inert and hidden", () => {
    render(<Sheet form="overlay" open={false} onClose={() => {}} label="Pages"><input defaultValue="draft" /></Sheet>);
    const box = document.querySelector(".vx-sheet") as HTMLElement;
    expect(box.hasAttribute("inert")).toBe(true);
    expect(box.getAttribute("aria-hidden")).toBe("true");
    expect((box.querySelector("input") as HTMLInputElement).value).toBe("draft");
  });

  it("the scrim closes it", () => {
    const onClose = vi.fn();
    render(<Sheet form="overlay" open onClose={onClose} label="Pages"><span>x</span></Sheet>);
    fireEvent.click(document.querySelector(".vx-scrim") as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Menu", () => {
  const items = (fn: (k: string) => void) => [
    { key: "a", label: "Attach files", onSelect: () => fn("a") },
    { key: "d", label: "Dictate", onSelect: () => fn("d") },
    { key: "x", label: "Disabled", disabled: true, onSelect: () => fn("x") },
  ];

  it("a menu button: aria-haspopup, expanded state, items as menuitems", () => {
    render(<Menu label="More composer actions" items={items(() => {})} />);
    const t = screen.getByRole("button", { name: "More composer actions" });
    expect(t.getAttribute("aria-haspopup")).toBe("menu");
    expect(t.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(t);
    expect(t.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
  });

  it("ArrowDown opens on the first item; arrows wrap; typeahead jumps; Escape closes and refocuses the trigger", () => {
    render(<Menu label="More" items={items(() => {})} />);
    const t = screen.getByRole("button", { name: "More" });
    fireEvent.keyDown(t, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toBe("Attach files");
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toBe("Dictate");
    fireEvent.keyDown(menu, { key: "ArrowDown" });   // the disabled item is skipped
    expect(document.activeElement?.textContent).toBe("Attach files");
    fireEvent.keyDown(menu, { key: "d" });
    expect(document.activeElement?.textContent).toBe("Dictate");
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(t);
  });

  it("activating an item runs it and closes; a disabled item does nothing", () => {
    const fn = vi.fn();
    render(<Menu label="More" items={items(fn)} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByText("Disabled"));
    expect(fn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Dictate"));
    expect(fn).toHaveBeenCalledWith("d");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("checked items are menuitemradio with aria-checked", () => {
    render(<Menu label="Ws" items={[{ key: "p", label: "personal", checked: true, onSelect: () => {} }, { key: "b", label: "bank", checked: false, onSelect: () => {} }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Ws" }));
    const radios = screen.getAllByRole("menuitemradio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false"]);
  });
});

describe("OverflowStrip", () => {
  it("shows an overflow control listing every tab when one is out of view", () => {
    // jsdom has no layout: give the scroller and the tabs real rectangles.
    const rect = (l: number, w: number) => ({ left: l, right: l + w, width: w, top: 0, bottom: 20, height: 20, x: l, y: 0, toJSON: () => ({}) });
    const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("vx-strip-scroll")) return rect(0, 200) as DOMRect;
      const k = this.getAttribute("data-strip-key");
      return rect(k ? Number(k) * 90 : 0, k ? 80 : 0) as DOMRect;
    });
    const tabs = ["0", "1", "2", "3", "4", "5"];
    render(<OverflowStrip label="tabs" activeKey="0" items={tabs.map((k) => ({ key: k, label: `Tab ${k}`, onSelect: () => {} }))}>
      {tabs.map((k) => <span key={k} data-strip-key={k}>Tab {k}</span>)}
    </OverflowStrip>);
    const more = screen.getByRole("button", { name: /4 more tabs/ });
    fireEvent.click(more);
    expect(screen.getAllByRole("menuitemradio")).toHaveLength(6);
    spy.mockRestore();
  });

  it("no control when everything fits", () => {
    render(<OverflowStrip label="tabs" items={[{ key: "a", label: "A", onSelect: () => {} }]}><span data-strip-key="a">A</span></OverflowStrip>);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("the shell store — one key, one writer", () => {
  beforeEach(() => localStorage.clear());

  it("round-trips prefs and per-mode widths", () => {
    writeShellStore({ prefs: { railOpen: false, pagesOpen: true }, widths: { wide: { pages: 520 }, compact: { pages: 340 } } });
    expect(readShellStore()).toEqual({ prefs: { railOpen: false, pagesOpen: true }, widths: { wide: { pages: 520 }, compact: { pages: 340 } } });
  });

  it("junk JSON and wrong types fall back to defaults, field by field", () => {
    localStorage.setItem(SHELL_KEY, "{not json");
    expect(readShellStore().prefs).toEqual({ railOpen: true, pagesOpen: true });
    localStorage.setItem(SHELL_KEY, JSON.stringify({ prefs: { railOpen: "no" }, widths: { wide: { pages: "x", rail: 250 }, bogus: { pages: 1 } } }));
    expect(readShellStore()).toEqual({ prefs: { railOpen: true, pagesOpen: true }, widths: { wide: { rail: 250 } } });
  });

  it("storage that throws still yields a working layout", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    expect(readShellStore().prefs).toEqual({ railOpen: true, pagesOpen: true });
    spy.mockRestore();
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    expect(() => writeShellStore({ prefs: { railOpen: true, pagesOpen: true }, widths: {} })).not.toThrow();
    set.mockRestore();
  });
});

// keep `act` referenced for environments that warn without it
void act;
