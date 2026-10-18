/** Founder 2026-10-16: on a meeting page, selecting text in the notes or the transcript offers
 *  "Reference" (a typed meeting chip + the quoted span and its time into the centre composer) and
 *  "✦ Ask about this" (the same plus a question). The centre chat never changes. */
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { ASK_CHAT_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { SelectionAct, referenceInChat } from "../ExtendAction";

const asks: Record<string, unknown>[] = [];
const sessions: unknown[] = [];
window.addEventListener(ASK_CHAT_EVENT, (e) => asks.push((e as CustomEvent).detail));
window.addEventListener(OPEN_SESSION_EVENT, (e) => sessions.push((e as CustomEvent).detail));
afterEach(() => { cleanup(); asks.length = 0; sessions.length = 0; });

describe("referenceInChat on a meeting", () => {
  it("puts a typed meeting chip, the quote and its time in the composer", () => {
    referenceInChat("the venue holds 120", { meeting: "142", at: "2026-10-16T10:12:00.000Z", speaker: "Valeriia" }, { label: "Venue & sponsors check-in" });
    const d = asks.at(-1)!;
    expect(d.mode).toBe("draft");
    expect(String(d.prompt)).toContain("[[meeting:142|Venue & sponsors check-in]]");
    expect(String(d.prompt)).toContain("> the venue holds 120");
    expect(String(d.prompt)).toMatch(/Valeriia/);
    expect(sessions).toEqual([]);
  });
  it("Ask adds a question to answer", () => {
    referenceInChat("the venue holds 120", { meeting: "142" }, { ask: true });
    expect(String(asks.at(-1)!.prompt)).toMatch(/Question: $/);
  });
});

function Host() {
  const box = useRef<HTMLDivElement>(null);
  return <div ref={box} style={{ position: "relative" }}><p data-testid="t">the venue holds 120 people</p>
    <SelectionAct containerRef={box} act="extend-notes" slot="m142" onReference={(text, ask) => referenceInChat(text, { meeting: "142" }, { ask })} /></div>;
}

describe("the selection pill", () => {
  it("offers Reference and Ask about this", () => {
    render(<Host />);
    const node = screen.getByTestId("t").firstChild!;
    act(() => {
      const r = document.createRange(); r.setStart(node, 4); r.setEnd(node, 9);
      const s = window.getSelection()!; s.removeAllRanges(); s.addRange(r);
      document.dispatchEvent(new Event("selectionchange"));
    });
    fireEvent.click(screen.getByRole("button", { name: /Reference/ }));
    expect(String(asks.at(-1)!.prompt)).toContain("> venue");
    act(() => {
      const r = document.createRange(); r.setStart(node, 4); r.setEnd(node, 9);
      const s = window.getSelection()!; s.removeAllRanges(); s.addRange(r);
      document.dispatchEvent(new Event("selectionchange"));
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask about this/ }));
    expect(String(asks.at(-1)!.prompt)).toMatch(/Question: $/);
  });
});
