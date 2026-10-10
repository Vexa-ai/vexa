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
 *
 *  And, since Vexa-ai/vexa#1595, the loudest silent failure of all: the chips were only ever in
 *  this tab's memory, so a reload showed a plain transcript again. The last block covers the map
 *  the canvas now READS — on open and after each Highlight — from the server that stored it.
 */
import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ASK_CHAT_EVENT, OPEN_ENTITY_EVENT } from "../../platform";
import { LiveTranscriptEngine } from "../LiveTranscriptEngine";
import { HighlightButton, TermText, useTermRenderer } from "../TranscriptTermControls";
import {
  loadTerms, mergeTerms, notePageWritten, promoteWritten, recordTerms, resetTerms, TERMS_EVENT,
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

// `useTranscriptTerms` reads the stored map on mount, so every test that mounts it makes a request.
// The default answer is "no map" — the shape of a meeting nobody highlighted — and the blocks that
// care about the read install their own. Left to the real `fetch`, a relative URL under jsdom would
// make each test's behaviour depend on how undici fails that day.
const NO_MAP = (() => Promise.resolve({ ok: false, status: 404 } as Response)) as typeof fetch;
const serves = (body: unknown, asked?: string[]) => ((url: string) => {
  asked?.push(String(url));
  return Promise.resolve({ ok: true, json: async () => body } as Response);
}) as unknown as typeof fetch;
const realFetch = globalThis.fetch;

beforeEach(() => {
  resetTerms(); asks = []; opened = [];
  globalThis.fetch = NO_MAP;
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
  window.addEventListener(OPEN_ENTITY_EVENT, onOpen);
});
afterEach(() => {
  globalThis.fetch = realFetch;
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  window.removeEventListener(OPEN_ENTITY_EVENT, onOpen);
});

async function renderAsync(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(ui); });
  return { container, unmount: () => { act(() => root.unmount()); container.remove(); } };
}

// ── the record ───────────────────────────────────────────────────────────────────────────────────

describe("the terms are the chat's record, and a second Highlight ADDS to it", () => {
  it("merges rather than replaces — nothing already chipped disappears on the next press", () => {
    const out = mergeTerms([unknown("Northwind Labs")], [unknown("Fernhill Loyalty Card")]);
    expect(out.map((t) => t.term)).toEqual(["Northwind Labs", "Fernhill Loyalty Card"]);
  });

  it("a later answer about `known` wins, including a later null", () => {
    expect(mergeTerms([unknown("Northwind Labs")], [known("Northwind Labs")])[0].known).toBeTruthy();
    // the page could have been deleted; a chip that stays solid over a page that is gone is the
    // "opens nothing" failure the link resolver already refuses
    expect(mergeTerms([known("Northwind Labs")], [unknown("Northwind Labs")])[0].known).toBeNull();
    expect(mergeTerms([unknown("Northwind Labs")], [known("northwind labs")])).toHaveLength(1);
  });

  it("the cursor only ever moves forward to what the SERVER issued", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [unknown("Northwind Labs")] });
    expect(termsCursor("41")).toBe("c9");
    // an event without a cursor must not reset it — the next Highlight would re-scan the whole room
    recordTerms({ meeting: "41", terms: [unknown("Fernhill Loyalty Card")] });
    expect(termsCursor("41")).toBe("c9");
    expect(termsFor("41")).toHaveLength(2);
  });

  it("an empty publish is a NON-event — it never clears what is on screen", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [unknown("Northwind Labs")] });
    recordTerms({ meeting: "41", cursor: "c12", terms: [] });
    expect(termsFor("41")).toHaveLength(1);
    expect(termsCursor("41")).toBe("c9");
  });

  it("terms belong to their own meeting", () => {
    recordTerms({ meeting: "41", cursor: "a", terms: [unknown("Northwind Labs")] });
    expect(termsFor("42")).toEqual([]);
  });
});

describe("a page the turn just wrote turns its chip solid, without asking anybody", () => {
  it("promotes on the entity path the agent wrote", () => {
    const out = promoteWritten([unknown("Northwind Labs")], "w1", "kg/entities/company/northwind-labs.md");
    expect(out[0].known).toEqual({ workspace_id: "w1", entity_id: "northwind-labs", path: "kg/entities/company/northwind-labs.md" });
    expect(out[0].kind).toBe("company");
  });

  it("returns the SAME array when nothing matched — a commit elsewhere must not churn the transcript", () => {
    const before = [unknown("Northwind Labs")];
    expect(promoteWritten(before, "w1", "README.md")).toBe(before);
    expect(promoteWritten(before, "w1", "kg/entities/company/other.md")).toBe(before);
  });

  it("the artifact event drives it across every meeting on screen", () => {
    recordTerms({ meeting: "41", cursor: "a", terms: [unknown("Northwind Labs")] });
    notePageWritten("w1", "kg/entities/company/northwind-labs.md");
    expect(termsFor("41")[0].known?.entity_id).toBe("northwind-labs");
  });
});

// ── the chips ────────────────────────────────────────────────────────────────────────────────────

describe("the chips", () => {
  it("solid for a term with a page, dashed for one without — and the words are never altered", () => {
    const { container, unmount } = render(
      <TermText text="Northwind Labs met Fernhill Loyalty Card today." meeting="41"
                terms={[known("Northwind Labs"), unknown("Fernhill Loyalty Card")]} />);
    expect(container.textContent).toBe("Northwind Labs met Fernhill Loyalty Card today.");
    const chips = [...container.querySelectorAll("[data-term]")] as HTMLElement[];
    expect(chips.map((c) => c.dataset.term)).toEqual(["Northwind Labs", "Fernhill Loyalty Card"]);
    expect(chips[0].dataset.known).toBe("1");
    expect(chips[1].dataset.known).toBe("0");
    expect(chips[1].style.borderBottom).toContain("dashed");
    unmount();
  });

  it("is a real button, so a keyboard reaches it and a screen reader is told what it does", () => {
    const { container, unmount } = render(
      <TermText text="Northwind Labs asked." meeting="41" terms={[unknown("Northwind Labs")]} />);
    const chip = container.querySelector("[data-term]") as HTMLButtonElement;
    expect(chip.tagName).toBe("BUTTON");
    expect(chip.type).toBe("button");
    expect(chip.getAttribute("aria-label")).toBe("Find out what Northwind Labs is");
    unmount();
  });

  it("a SOLID chip navigates the view slot through the resolver — it never mints a tab", () => {
    const { container, unmount } = render(
      <TermText text="Northwind Labs asked." meeting="41" terms={[known("Northwind Labs")]} />);
    act(() => { (container.querySelector("[data-term]") as HTMLElement).click(); });
    expect(opened).toEqual([{ path: "kg/entities/company/northwind-labs.md" }]);
    expect(asks).toHaveLength(0);
    unmount();
  });

  it("a DASHED chip drops an `explore` into the open chat, as a compact bubble", () => {
    const { container, unmount } = render(
      <TermText text="Northwind Labs asked." meeting="41" segment="s7" terms={[unknown("Northwind Labs")]} />);
    act(() => { (container.querySelector("[data-term]") as HTMLElement).click(); });
    expect(asks).toHaveLength(1);
    expect(asks[0].display).toBe("Explore: Northwind Labs");
    expect(asks[0].intent).toEqual({ kind: "explore", term: "Northwind Labs", meeting: "41", segment: "s7" });
    // the fallback sentence carries the whole ask, so a deployment whose preset library is behind
    // the client still does the right thing in plainer words
    expect(asks[0].prompt).toContain("Explore `Northwind Labs`");
    expect(asks[0].prompt).toContain("segment s7");
    expect(asks[0].hidden).toBeUndefined();
    expect(opened).toHaveLength(0);
    unmount();
  });

  it("the longest term wins where two overlap", () => {
    const { container, unmount } = render(
      <TermText text="Fernhill Loyalty Card called." meeting="41"
                terms={[unknown("Fernhill Loyalty"), known("Fernhill Loyalty Card")]} />);
    const chips = [...container.querySelectorAll("[data-term]")] as HTMLElement[];
    expect(chips.map((c) => c.dataset.term)).toEqual(["Fernhill Loyalty Card"]);
    unmount();
  });

  it("a chip is not rebuilt when an unrelated line arrives — a rebuild drops keyboard focus off it", () => {
    const terms = [unknown("Northwind Labs")];
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const view = () => <TermText text="Northwind Labs asked." meeting="41" terms={terms} />;
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
  const segments = [{ id: "s0", speaker: "Jane", text: "Northwind Labs asked about pricing.", completed: true }];

  function Live({ meeting }: { meeting: string }) {
    return <LiveTranscriptEngine segments={segments} renderText={useTermRenderer(meeting)} />;
  }

  it("plain text until something is published — an un-highlighted meeting costs nothing", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    expect(container.textContent).toContain("Northwind Labs asked about pricing.");
    expect(container.querySelector("[data-term]")).toBeNull();
    unmount();
  });

  it("a `terms` event on the window paints the chips, live and completed alike", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    act(() => {
      window.dispatchEvent(new CustomEvent(TERMS_EVENT, {
        detail: { meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] },
      }));
    });
    expect((container.querySelector("[data-term]") as HTMLElement).dataset.term).toBe("Northwind Labs");
    expect(container.textContent).toContain("Northwind Labs asked about pricing.");
    unmount();
  });

  it("an event for ANOTHER meeting never reaches this transcript", () => {
    const { container, unmount } = render(<Live meeting="41" />);
    act(() => {
      window.dispatchEvent(new CustomEvent(TERMS_EVENT, {
        detail: { meeting: "99", cursor: "c9", terms: [known("Northwind Labs")] },
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
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] });
    const { container, unmount } = render(<HighlightButton meeting="41" />);
    act(() => { (container.querySelector('[data-act="highlight"]') as HTMLElement).click(); });
    expect(asks[0].intent).toEqual({ kind: "highlight", meeting: "41", since: "c9" });
    unmount();
  });

  it("says how many terms this transcript already carries", () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs"), unknown("Fernhill Loyalty Card")] });
    const { container, unmount } = render(<HighlightButton meeting="41" />);
    expect(container.textContent).toContain("Highlight · 2");
    unmount();
  });
});

describe("termSpans", () => {
  it("carries the chip STATE as the span kind, which is the only thing painted differently", () => {
    expect(termSpans([known("Northwind Labs"), unknown("Fernhill Loyalty Card")]).map((s) => s.kind))
      .toEqual(["known", "unknown"]);
  });

  it("drops a one-character term — a span that short is a false match, not a name", () => {
    expect(termSpans([unknown("A")])).toHaveLength(0);
  });
});

// ── the map that outlives the turn (Vexa-ai/vexa#1595) ───────────────────────────────────────────
//
// Founder, mid-meeting with Highlight pressed: *"we want transcript being attributed with extracted
// entities when we get highlight — it should attribute the transcript in an efficient way (no
// rewrite)"*. The event paints NOW; the stored map is what makes the chips still be there after a
// reload, in a second tab, and on a meeting reopened tomorrow. Nothing here rewrites a segment: the
// map is surface forms, and the renderer re-finds them in the words it is already drawing.

describe("the annotation layer is read from the server, so a reload keeps the chips", () => {
  const MAP = { meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] };

  it("reads the stored map into a store that knows nothing — the reload this route exists for", async () => {
    const asked: string[] = [];
    await loadTerms("41", serves(MAP, asked));
    expect(asked).toEqual(["/api/meeting/terms?meeting_id=41"]);
    expect(termsFor("41").map((t) => t.term)).toEqual(["Northwind Labs"]);
    expect(termsCursor("41")).toBe("c9");
  });

  it("MERGES with what is already on screen — a read can never un-paint a live publish", async () => {
    recordTerms({ meeting: "41", cursor: "c12", terms: [unknown("Fernhill Loyalty Card")] });
    await loadTerms("41", serves(MAP));
    expect(termsFor("41").map((t) => t.term)).toEqual(["Fernhill Loyalty Card", "Northwind Labs"]);
  });

  it("an empty stored map is a non-event, never a clear", async () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] });
    await loadTerms("41", serves({ meeting: "41", cursor: "", terms: [] }));
    expect(termsFor("41")).toHaveLength(1);
    expect(termsCursor("41")).toBe("c9");
  });

  it("a read that failed costs the transcript its chips, never its text and never an error", async () => {
    recordTerms({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] });
    await loadTerms("41", (() => Promise.reject(new Error("offline"))) as unknown as typeof fetch);
    await loadTerms("41", NO_MAP);
    expect(termsFor("41")).toHaveLength(1);
  });

  it("asks once per meeting however many components subscribe to it", async () => {
    const asked: string[] = [];
    const fetcher = serves(MAP, asked);
    await Promise.all([loadTerms("41", fetcher), loadTerms("41", fetcher)]);
    expect(asked).toHaveLength(1);
  });
});

describe("the transcript paints from the stored map, with no event at all", () => {
  const segments = [{ id: "s0", speaker: "Jane", text: "Northwind Labs asked about pricing.", completed: true }];

  function Live({ meeting }: { meeting: string }) {
    return <LiveTranscriptEngine segments={segments} renderText={useTermRenderer(meeting)} />;
  }

  it("the canvas asks on OPEN, and the words are never altered", async () => {
    const asked: string[] = [];
    globalThis.fetch = serves({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] }, asked);
    const { container, unmount } = await renderAsync(<Live meeting="41" />);
    expect(asked).toEqual(["/api/meeting/terms?meeting_id=41"]);
    expect((container.querySelector("[data-term]") as HTMLElement).dataset.term).toBe("Northwind Labs");
    expect(container.textContent).toContain("Northwind Labs asked about pricing.");
    unmount();
  });

  it("a chip that came off the server opens the entity page, like any other", async () => {
    globalThis.fetch = serves({ meeting: "41", cursor: "c9", terms: [known("Northwind Labs")] });
    const { container, unmount } = await renderAsync(<Live meeting="41" />);
    await act(async () => { (container.querySelector("[data-term]") as HTMLElement).click(); });
    expect(opened).toEqual([{ path: "kg/entities/company/northwind-labs.md" }]);
    unmount();
  });

  it("a Highlight completion re-reads the map — the event is this turn, the server is every turn", async () => {
    const asked: string[] = [];
    globalThis.fetch = serves({ meeting: "41", cursor: "c12", terms: [known("Northwind Labs"), unknown("Acme")] }, asked);
    const { container, unmount } = await renderAsync(<Live meeting="41" />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent(TERMS_EVENT, {
        detail: { meeting: "41", cursor: "c12", terms: [unknown("Acme")] },
      }));
    });
    expect(asked).toHaveLength(2);
    expect(termsFor("41").map((t) => t.term)).toEqual(["Northwind Labs", "Acme"]);
    expect(container.textContent).toContain("Northwind Labs asked about pricing.");
    unmount();
  });

  it("an un-highlighted meeting still costs nothing — no map, no chips, plain text", async () => {
    const { container, unmount } = await renderAsync(<Live meeting="41" />);
    expect(container.querySelector("[data-term]")).toBeNull();
    expect(container.textContent).toContain("Northwind Labs asked about pricing.");
    unmount();
  });
});
