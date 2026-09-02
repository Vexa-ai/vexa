/** PRD decision 35, client half — chips over the transcript, and the two clicks they carry.
 *
 *  Founder: *"click on a thing and it's dropped into the chat as a research drop… just to find out
 *  what that is"*, and the correction that made the trigger a button: *"we will have a button on
 *  transcripts that will silently request our open chat to deliver the important and new terms."*
 *
 *  The behaviours worth a test are the ones that fail SILENTLY: a merge that replaces instead of
 *  adding (chips vanish on the second press), a cursor the client invents (the whole room is
 *  re-scanned every time), a chip that mints a tab (seven tabs after a few clicks), and a Highlight
 *  that reaches the person as a bubble.
 */
import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ASK_CHAT_EVENT, OPEN_ENTITY_EVENT } from "../actions";
import { LiveTranscriptEngine } from "../LiveTranscriptEngine";
import { HighlightButton, TermText, useTermRenderer } from "../TranscriptTerms";
import {
  mergeTerms, notePageWritten, promoteWritten, recordTerms, resetTerms, TERMS_EVENT,
  termsCursor, termsFor, termSpans, type TranscriptTerm,
} from "../transcriptTerms";

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(ui); });
  return { container, unmount: () => { act(() => root.unmount()); container.remove(); } };
}

const known = (term: string): TranscriptTerm =>
  ({ term, kind: "company", known: { workspace_id: "w1", entity_id: term.toLowerCase().replace(/\s+/g, "-"), path: `kg/entities/company/${term.toLowerCase().replace(/\s+/g, "-")}.md` } });
const unknown = (term: string): TranscriptTerm => ({ term, known: null });

let asks: { display?: string; prompt?: string; hidden?: boolean; intent?: unknown }[] = [];
let opened: { path?: string }[] = [];
const onAsk = (e: Event) => { asks.push((e as CustomEvent).detail); };
const onOpen = (e: Event) => { opened.push((e as CustomEvent).detail); };

beforeEach(() => {
  resetTerms(); asks = []; opened = [];
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
  window.addEventListener(OPEN_ENTITY_EVENT, onOpen);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  window.removeEventListener(OPEN_ENTITY_EVENT, onOpen);
});

// ── the record ───────────────────────────────────────────────────────────────────────────────────

describe("the terms are the chat's record, and a second Highlight ADDS to it", () => {
  it("merges rather than replaces — nothing already chipped disappears on the next press", () => {
    const out = mergeTerms([unknown("Kaar Tech")], [unknown("Blue Light Card")]);
    expect(out.map((t) => t.term)).toEqual(["Kaar Tech", "Blue Light Card"]);
  });

  it("a later answer about `known` wins, including a later null", () => {
    expect(mergeTerms([unknown("Kaar Tech")], [known("Kaar Tech")])[0].known).toBeTruthy();
    // the page could have been deleted; a chip that stays solid over a page that is gone is the
    // "opens nothing" failure the link resolver already refuses
    expect(mergeTerms([known("Kaar Tech")], [unknown("Kaar Tech")])[0].known).toBeNull();
    expect(mergeTerms([unknown("Kaar Tech")], [known("kaar tech")])).toHaveLength(1);
  });

  it("the cursor only ever moves forward to what the SERVER issued", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [unknown("Kaar Tech")] });
    expect(termsCursor("41")).toBe("c9");
    // an event without a cursor must not reset it — the next Highlight would re-scan the whole room
    recordTerms({ meeting: "41", terms: [unknown("Blue Light Card")] });
    expect(termsCursor("41")).toBe("c9");
    expect(termsFor("41")).toHaveLength(2);
  });

  it("an empty publish is a NON-event — it never clears what is on screen", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [unknown("Kaar Tech")] });
    recordTerms({ meeting: "41", cursor: "c12", terms: [] });
    expect(termsFor("41")).toHaveLength(1);
    expect(termsCursor("41")).toBe("c9");
  });

  it("terms belong to their own meeting", () => {
    recordTerms({ meeting: "41", cursor: "a", terms: [unknown("Kaar Tech")] });
    expect(termsFor("42")).toEqual([]);
  });
});

describe("a page the turn just wrote turns its chip solid, without asking anybody", () => {
  it("promotes on the entity path the agent wrote", () => {
    const out = promoteWritten([unknown("Kaar Tech")], "w1", "kg/entities/company/kaar-tech.md");
    expect(out[0].known).toEqual({ workspace_id: "w1", entity_id: "kaar-tech", path: "kg/entities/company/kaar-tech.md" });
    expect(out[0].kind).toBe("company");
  });

  it("returns the SAME array when nothing matched — a commit elsewhere must not churn the transcript", () => {
    const before = [unknown("Kaar Tech")];
    expect(promoteWritten(before, "w1", "README.md")).toBe(before);
    expect(promoteWritten(before, "w1", "kg/entities/company/other.md")).toBe(before);
  });

  it("the artifact event drives it across every meeting on screen", () => {
    recordTerms({ meeting: "41", cursor: "a", terms: [unknown("Kaar Tech")] });
    notePageWritten("w1", "kg/entities/company/kaar-tech.md");
    expect(termsFor("41")[0].known?.entity_id).toBe("kaar-tech");
  });
});

// ── the chips ────────────────────────────────────────────────────────────────────────────────────

describe("the chips", () => {
  it("solid for a term with a page, dashed for one without — and the words are never altered", () => {
    const { container, unmount } = render(
      <TermText text="Kaar Tech met Blue Light Card today." meeting="41"
                terms={[known("Kaar Tech"), unknown("Blue Light Card")]} />);
    expect(container.textContent).toBe("Kaar Tech met Blue Light Card today.");
    const chips = [...container.querySelectorAll("[data-term]")] as HTMLElement[];
    expect(chips.map((c) => c.dataset.term)).toEqual(["Kaar Tech", "Blue Light Card"]);
    expect(chips[0].dataset.known).toBe("1");
    expect(chips[1].dataset.known).toBe("0");
    expect(chips[1].style.borderBottom).toContain("dashed");
    unmount();
  });

  it("is a real button, so a keyboard reaches it and a screen reader is told what it does", () => {
    const { container, unmount } = render(
      <TermText text="Kaar Tech asked." meeting="41" terms={[unknown("Kaar Tech")]} />);
    const chip = container.querySelector("[data-term]") as HTMLButtonElement;
    expect(chip.tagName).toBe("BUTTON");
    expect(chip.type).toBe("button");
    expect(chip.getAttribute("aria-label")).toBe("Find out what Kaar Tech is");
    unmount();
  });

  it("a SOLID chip navigates the view slot through the resolver — it never mints a tab", () => {
    const { container, unmount } = render(
      <TermText text="Kaar Tech asked." meeting="41" terms={[known("Kaar Tech")]} />);
    act(() => { (container.querySelector("[data-term]") as HTMLElement).click(); });
    expect(opened).toEqual([{ path: "kg/entities/company/kaar-tech.md" }]);
    expect(asks).toHaveLength(0);
    unmount();
  });

  it("a DASHED chip drops an `explore` into the open chat, as a compact bubble", () => {
    const { container, unmount } = render(
      <TermText text="Kaar Tech asked." meeting="41" segment="s7" terms={[unknown("Kaar Tech")]} />);
    act(() => { (container.querySelector("[data-term]") as HTMLElement).click(); });
    expect(asks).toHaveLength(1);
    expect(asks[0].display).toBe("Explore: Kaar Tech");
    expect(asks[0].intent).toEqual({ kind: "explore", term: "Kaar Tech", meeting: "41", segment: "s7" });
    // the fallback sentence carries the whole ask, so a deployment whose preset library is behind
    // the client still does the right thing in plainer words
    expect(asks[0].prompt).toContain("Explore `Kaar Tech`");
    expect(asks[0].prompt).toContain("segment s7");
    expect(asks[0].hidden).toBeUndefined();
    expect(opened).toHaveLength(0);
    unmount();
  });

  it("the longest term wins where two overlap", () => {
    const { container, unmount } = render(
      <TermText text="Blue Light Card called." meeting="41"
                terms={[unknown("Blue Light"), known("Blue Light Card")]} />);
    const chips = [...container.querySelectorAll("[data-term]")] as HTMLElement[];
    expect(chips.map((c) => c.dataset.term)).toEqual(["Blue Light Card"]);
    unmount();
  });

  it("a chip is not rebuilt when an unrelated line arrives — a rebuild drops keyboard focus off it", () => {
    const terms = [unknown("Kaar Tech")];
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const view = () => <TermText text="Kaar Tech asked." meeting="41" terms={terms} />;
    act(() => { root.render(view()); });
    const first = container.querySelector("[data-term]");
    act(() => { root.render(view()); });
    expect(container.querySelector("[data-term]")).toBe(first);
    act(() => { root.unmount(); });
    container.remove();
  });
});

// ── the engine seam ──────────────────────────────────────────────────────────────────────────────

describe("the transcript renders the layer without knowing anything about it", () => {
  const segments = [{ id: "s0", speaker: "Jane", text: "Kaar Tech asked about pricing.", completed: true }];

  function Live({ meeting }: { meeting: string }) {
    return <LiveTranscriptEngine segments={segments} renderText={useTermRenderer(meeting)} />;
  }

  it("plain text until something is published — an un-highlighted meeting costs nothing", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    expect(container.textContent).toContain("Kaar Tech asked about pricing.");
    expect(container.querySelector("[data-term]")).toBeNull();
    unmount();
  });

  it("a `terms` event on the window paints the chips, live and completed alike", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    act(() => {
      window.dispatchEvent(new CustomEvent(TERMS_EVENT, {
        detail: { meeting: "41", cursor: "c9", terms: [known("Kaar Tech")] },
      }));
    });
    expect((container.querySelector("[data-term]") as HTMLElement).dataset.term).toBe("Kaar Tech");
    expect(container.textContent).toContain("Kaar Tech asked about pricing.");
    unmount();
  });

  it("an event for ANOTHER meeting never reaches this transcript", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    act(() => {
      window.dispatchEvent(new CustomEvent(TERMS_EVENT, {
        detail: { meeting: "99", cursor: "c9", terms: [known("Kaar Tech")] },
      }));
    });
    expect(container.querySelector("[data-term]")).toBeNull();
    unmount();
  });
});

// ── the button ───────────────────────────────────────────────────────────────────────────────────

describe("the Highlight button", () => {
  it("posts a SILENT intent — no bubble, no words the person did not type", () => {
    const { container, unmount } = render(<HighlightButton meeting="41" />);
    act(() => { (container.querySelector('[data-act="highlight"]') as HTMLElement).click(); });
    expect(asks).toHaveLength(1);
    expect(asks[0].hidden).toBe(true);
    expect(asks[0].intent).toEqual({ kind: "highlight", meeting: "41" });
    unmount();
  });

  it("sends back the cursor the last publish issued, so a second press adds only what is new", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Kaar Tech")] });
    const { container, unmount } = render(<HighlightButton meeting="41" />);
    act(() => { (container.querySelector('[data-act="highlight"]') as HTMLElement).click(); });
    expect(asks[0].intent).toEqual({ kind: "highlight", meeting: "41", since: "c9" });
    unmount();
  });

  it("says how many terms this transcript already carries", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Kaar Tech"), unknown("Blue Light Card")] });
    const { container, unmount } = render(<HighlightButton meeting="41" />);
    expect(container.textContent).toContain("Highlight · 2");
    unmount();
  });
});

describe("termSpans", () => {
  it("carries the chip STATE as the span kind, which is the only thing painted differently", () => {
    expect(termSpans([known("Kaar Tech"), unknown("Blue Light Card")]).map((s) => s.kind))
      .toEqual(["known", "unknown"]);
  });

  it("drops a one-character term — a span that short is a false match, not a name", () => {
    expect(termSpans([unknown("A")])).toHaveLength(0);
  });
});
