/** EXTEND ON THE MEETING'S OWN PAGE (Vexa-ai/vexa#1598).
 *
 *  Founder, live, 2026-09-06: *"how can we have a meeting artefact that is being updated on meeting
 *  on person clicking expand? that would read transcript."* So the act has a meeting-doc variant,
 *  and this file pins the ONE thing the client owes it: the page's own binding travels with the
 *  press, so the server can run the right ask.
 *
 *  THE BINDING IS READ OFF THE DOCUMENT, never off the shell. A meeting doc declares its transcript
 *  widget in its own source; that declaration is the fact. An answer derived from "which chat is
 *  open" would follow the reader's tabs, and the act is about the page in front of them — the same
 *  reason the intent is built from the resolved view slot rather than from a tab label (F63).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../../ui-kit/MdxDoc", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  const { createElement } = await import("react");
  return { ...orig, MdxDoc: (p: { children?: unknown }) => createElement("div", { "data-mdx": "" }, p.children as never) };
});

import { PagesPanel } from "../PagesPanel";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { clearPending } from "../extend";
import { transcriptSlotMarker } from "../../ui-kit/transcriptSlot";
import type { PageIntent } from "../../surfaces/chatIntent";
import type { Page } from "../types";

const PATH = "kg/entities/meeting/2026-03-02-0000-dna-tsc.md";
const MEETING_DOC = [
  "---", "type: meeting", "meeting: 147", "transcript_cursor: 2026-09-06T12:04:31.000Z", "---",
  "", "# DNA TSC 2026-03-02", "", transcriptSlotMarker("147"), "",
  "## Decisions", "<!-- meeting:decisions:start -->", "- The CLA follows the ASWF shape.",
  "<!-- meeting:decisions:end -->", "",
].join("\n");
const PLAIN_DOC = "# Helm Bank\n\nThe pilot ships in March, self-hosted.\n";

const pages: Page[] = [{ path: PATH, label: "DNA TSC 2026-03-02" }];
const asks: { prompt?: string; display?: string; intent?: PageIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const onOpen = vi.fn();

const fireLine = () => fireEvent.keyDown(document.querySelector("[data-act-field]") as HTMLElement, { key: "Escape" });

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={pages} docPath={PATH} onOpen={onOpen} body={MEETING_DOC} {...over} />);

beforeEach(() => { asks.length = 0; onOpen.mockClear(); clearPending(); window.addEventListener(ASK_CHAT_EVENT, onAsk); });
afterEach(() => { window.removeEventListener(ASK_CHAT_EVENT, onAsk); cleanup(); });

describe("a page that declares a transcript widget", () => {
  it("carries the meeting on the Extend intent, read off the page itself", () => {
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    fireLine();
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({ kind: "extend", path: PATH, meeting: "147" });
  });

  it("says on the control that it reads what is new — nobody presses it to find out", () => {
    const { container } = panel();
    const title = container.querySelector('[data-doc-act="extend"] [data-act-title]');
    expect(title?.textContent).toContain("meeting");
    expect(container.querySelector('[data-doc-act="extend"] [data-act-line]')?.textContent)
      .toContain("since last time");
  });

  it("the fallback sentence carries the two rules whose absence is invisible", () => {
    // the `extend-meeting` ask says all of this properly; this sentence is what runs on an instance
    // whose preset library predates it, and it must still say: read since the cursor, keep the slot.
    const { container } = panel();
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    fireLine();
    const prompt = asks[0].prompt ?? "";
    expect(prompt).toContain("transcript_cursor");
    expect(prompt).toContain('meeting_transcript(meeting_id="147"');
    expect(prompt).toContain("vexa:transcript");
    expect(prompt).toContain("meeting:<key>:start");
    // and the bubble stays a label — the machinery never becomes the person's own words
    expect(asks[0].display).toBe(`Extend: ${PATH}`);
  });

  it("a selection on the page carries the meeting too", () => {
    const { container } = panel();
    const body = container.querySelector("[data-doc-body]") as HTMLElement;
    const node = container.querySelector("[data-mdx]")?.firstChild as Node;
    const range = document.createRange();
    range.setStart(node, MEETING_DOC.indexOf("The CLA"));
    range.setEnd(node, MEETING_DOC.indexOf("The CLA") + "The CLA".length);
    const sel = window.getSelection();
    sel?.removeAllRanges(); sel?.addRange(range);
    fireEvent(document, new Event("selectionchange"));
    expect(body).toBeTruthy();
    fireEvent.mouseDown(container.querySelector('[data-doc-act="extend-selection"]') as HTMLElement);
    fireLine();
    expect(asks[0].intent).toMatchObject({ kind: "extend", path: PATH, meeting: "147", selection: "The CLA" });
  });
});

describe("every other page in the product", () => {
  it("names no meeting, and reads exactly as it did before the variant existed", () => {
    const { container } = panel({ body: PLAIN_DOC, docPath: "kg/entities/company/helm.md",
                                  pages: [{ path: "kg/entities/company/helm.md", label: "helm" }] });
    expect(container.querySelector('[data-doc-act="extend"] [data-act-title]')?.textContent)
      .toBe("Extend this page");
    fireEvent.click(container.querySelector('[data-doc-act="extend"]') as HTMLElement);
    fireLine();
    expect(asks[0].intent).toEqual({ kind: "extend", path: "kg/entities/company/helm.md" });
    expect(asks[0].prompt).not.toContain("transcript_cursor");
  });
});
