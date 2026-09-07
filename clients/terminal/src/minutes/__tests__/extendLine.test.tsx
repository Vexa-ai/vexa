/** THE OPTIONAL LINE ON AN ACT (Vexa-ai/vexa#1593).
 *
 *  Founder, 2026-09-06, with "recorded YouTube video" selected on a page: *"extend might have an
 *  extra prompt that opens on click like 'find link on youtube i would add then'"*.
 *
 *  What has a plausible wrong answer here, and is therefore what this file pins:
 *   · a press that fires the act instead of opening the field (the old behaviour, silently kept);
 *   · Escape treated as CANCEL — the founder's field is optional, so dismissing it still extends;
 *   · an empty line reaching the wire as `instruction: ""`, which is not "they typed nothing";
 *   · the floating control unmounting itself the instant its own field takes focus and collapses
 *     the selection it is about — the one failure mode this feature has by construction;
 *   · the line leaking into the BUBBLE, which #1588 says stays the short act label.
 *
 *  Rendered through `PagesPanel`, like `extendPanel.test.tsx`, because the claim is about the two
 *  triggers as the reader meets them, not about a button in isolation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent, act } from "@testing-library/react";

// the rendered document as ONE text node — the selection tests need exact offsets
vi.mock("../../ui-kit/MdxDoc", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  const { createElement } = await import("react");
  return { ...orig, MdxDoc: (p: { children?: unknown }) => createElement("div", { "data-mdx": "" }, p.children as never) };
});

import { PagesPanel } from "../PagesPanel";
import { LINE_PLACEHOLDER } from "../ExtendAction";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { INSTRUCTION_LEAD, clearPending } from "../extend";
import { INSTRUCTION_MAX, normalizeIntent } from "../../surfaces/chatIntent";
import type { PageIntent } from "../../surfaces/chatIntent";
import type { Page } from "../types";

const PATH = "kg/entities/company/helm.md";
const BODY = "# Helm Bank\n\nThe pilot ships in March, self-hosted.\n";
const LINE = "find link on youtube i would add then";
const pages: Page[] = [{ path: PATH, slug: "acme-kg", label: "Some Other Name" }];

const asks: { prompt?: string; display?: string; intent?: PageIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const onOpen = vi.fn();

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={pages} docPath={PATH} docSlug="acme-kg" onOpen={onOpen} body={BODY} {...over} />);

/** the one-line field, wherever it is open */
const field = (root: HTMLElement) => root.querySelector("[data-act-field]") as HTMLInputElement | null;
const type = (input: HTMLInputElement, text: string) => fireEvent.change(input, { target: { value: text } });

beforeEach(() => {
  asks.length = 0; onOpen.mockClear(); clearPending();
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  cleanup();
});

describe("the page control opens a line before it fires", () => {
  it("a press opens the field and sends NOTHING yet", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);

    const input = field(container);
    expect(input).toBeTruthy();
    expect(input!.placeholder).toBe(LINE_PLACEHOLDER);
    expect(asks).toHaveLength(0);                       // the press is not the act
    expect(container.querySelector('[data-doc-act="extend"]')).toBeNull();   // the field took its place
  });

  it("Enter fires the act WITH the line, verbatim", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    type(field(container)!, LINE);
    fireEvent.keyDown(field(container)!, { key: "Enter" });

    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "extend", workspace: "acme-kg", path: PATH, instruction: LINE });
    expect(field(container)).toBeNull();                // fired and closed
    expect(onOpen).not.toHaveBeenCalled();              // still mints no tab (decision 28)
  });

  it("Escape fires the act WITHOUT the line — the field is optional, not a confirmation", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    type(field(container)!, "half a thought");
    fireEvent.keyDown(field(container)!, { key: "Escape" });

    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "extend", workspace: "acme-kg", path: PATH });
    expect("instruction" in asks[0].intent!).toBe(false);
  });

  it("an empty Enter is today's behaviour exactly — no field on the wire at all", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    fireEvent.keyDown(field(container)!, { key: "Enter" });

    expect(asks[0].intent).toEqual({ kind: "extend", workspace: "acme-kg", path: PATH });
    expect(asks[0].prompt).toBe(`Extend: ${PATH}`);
    expect(asks[0].display).toBe(`Extend: ${PATH}`);
  });

  it("whitespace alone is nothing typed", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    type(field(container)!, "    ");
    fireEvent.keyDown(field(container)!, { key: "Enter" });
    expect(asks[0].intent?.instruction).toBeUndefined();
  });

  it("a page that changes under an open field closes it — the line was about the other page", () => {
    const { container, rerender } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    expect(field(container)).toBeTruthy();

    rerender(<PagesPanel pages={pages} docPath="kg/other.md" docSlug="acme-kg" onOpen={onOpen} body={BODY} />);
    expect(field(container)).toBeNull();
    expect(asks).toHaveLength(0);
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

describe("the floating control on a selection takes the same line", () => {
  const rendered = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) => {
    const r = panel(over);
    return { ...r, doc: r.container.querySelector("[data-mdx]") as HTMLElement };
  };

  it("a press opens the field and sends nothing yet", () => {
    const { container, doc } = rendered();
    highlight(doc, 13, 24);                              // "The pilot ships in March"
    fireEvent.mouseDown(container.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);

    expect(field(container)).toBeTruthy();
    expect(asks).toHaveLength(0);
  });

  it("the field SURVIVES its own focus collapsing the selection it is about", () => {
    const { container, doc } = rendered();
    highlight(doc, 13, 24);
    fireEvent.mouseDown(container.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);

    // what focusing an input does to a document selection, exactly
    act(() => {
      (window.getSelection() as Selection).removeAllRanges();
      document.dispatchEvent(new Event("selectionchange"));
    });
    expect(field(container)).toBeTruthy();

    type(field(container)!, LINE);
    fireEvent.keyDown(field(container)!, { key: "Enter" });
    expect(asks[0].intent).toMatchObject({ selection: "The pilot ships in March", instruction: LINE });
  });

  it("the WHERE and the WHAT travel together — selection, its source range, and the line", () => {
    const { container, doc } = rendered();
    highlight(doc, 13, 24);
    fireEvent.mouseDown(container.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    type(field(container)!, LINE);
    fireEvent.keyDown(field(container)!, { key: "Enter" });

    const i = asks[0].intent!;
    expect(i.kind).toBe("extend");
    expect(i.path).toBe(PATH);
    expect(i.instruction).toBe(LINE);
    expect(BODY.slice(i.selection_range!.start, i.selection_range!.end)).toBe("The pilot ships in March");
  });

  it("Escape extends the selection with no line", () => {
    const { container, doc } = rendered();
    highlight(doc, 13, 24);
    fireEvent.mouseDown(container.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    fireEvent.keyDown(field(container)!, { key: "Escape" });

    expect(asks).toHaveLength(1);
    expect(asks[0].intent?.selection).toBe("The pilot ships in March");
    expect(asks[0].intent?.instruction).toBeUndefined();
  });
});

describe("Create takes the same line (#1592 — one control shape for the two acts)", () => {
  it("a press opens the field; Enter fires the [create] act with it", () => {
    const { container } = panel({ body: null });
    fireEvent.click(container.querySelector('[data-doc-act="create"]') as HTMLElement);
    expect(asks).toHaveLength(0);

    type(field(container)!, "start from the meeting note");
    fireEvent.keyDown(field(container)!, { key: "Enter" });
    expect(asks[0].intent).toEqual({ kind: "create", workspace: "acme-kg", path: PATH, instruction: "start from the meeting note" });
  });

  it("Escape creates the page the way it always did", () => {
    const { container } = panel({ body: null });
    fireEvent.click(container.querySelector('[data-doc-act="create"]') as HTMLElement);
    fireEvent.keyDown(field(container)!, { key: "Escape" });
    expect(asks[0].intent).toEqual({ kind: "create", workspace: "acme-kg", path: PATH });
  });
});

describe("what the line does to the two things the turn carries", () => {
  it("the AGENT gets it, attributed, in the fallback sentence", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    type(field(container)!, LINE);
    fireEvent.keyDown(field(container)!, { key: "Enter" });

    expect(asks[0].prompt).toBe(`Extend: ${PATH}\n\n${INSTRUCTION_LEAD}\n\n${LINE}`);
    expect(INSTRUCTION_LEAD).toContain("their own words");
  });

  it("the BUBBLE stays the short act label (#1588) — the person reads the act, not the machinery", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    type(field(container)!, LINE);
    fireEvent.keyDown(field(container)!, { key: "Enter" });

    expect(asks[0].display).toBe(`Extend: ${PATH}`);
    expect(asks[0].display).not.toContain(INSTRUCTION_LEAD);
  });
});

describe("what an instruction may say on the wire", () => {
  it("is absent, never an empty string, when nothing was typed", () => {
    const i = normalizeIntent({ kind: "extend", path: "a.md", instruction: "   " })!;
    expect(i.instruction).toBeUndefined();
    expect("instruction" in i).toBe(false);
  });

  it("stays ONE line — a paste with newlines in it does not break the block open", () => {
    expect(normalizeIntent({ kind: "extend", path: "a.md", instruction: " find the\n\nyoutube link " })!.instruction)
      .toBe("find the youtube link");
  });

  it("is capped — a one-line field is not a document", () => {
    const long = "x".repeat(INSTRUCTION_MAX + 500);
    expect(normalizeIntent({ kind: "extend", path: "a.md", instruction: long })!.instruction).toHaveLength(INSTRUCTION_MAX);
  });

  it("cannot rescue an intent that had nothing to act on", () => {
    expect(normalizeIntent({ kind: "extend", path: "", instruction: LINE })).toBeNull();
  });
});

// The LANDING is unchanged by the line and is pinned in `extendPanel.test.tsx`; nothing here
// re-states it.
