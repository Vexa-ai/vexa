/** The transcript with signals on it: a small chip on the line that was a decision / action /
 *  blocker / said to Vexa, a few words in a heavier weight — and, without any annotations, the very
 *  same transcript as before. */
import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { LiveTranscriptEngine } from "../LiveTranscriptEngine";
import { marksFor } from "../signalMarks";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(ui); });
  return { container, unmount: () => act(() => root.unmount()) };
}

const segments = [
  { id: "s1", speaker: "Jane", text: "We ship the proxy change on Friday.", completed: true },
  { id: "s2", speaker: "Jane", text: "Vexa, note that down please.", completed: true },
  { id: "s3", speaker: "Ravi", text: "Sounds good.", completed: true },
];
const annotations = {
  s1: { id: "1-0", segment_id: "s1", scores: { decision: 0.92 }, words: { Friday: 0.9, proxy: 0.8 } },
  s2: { id: "2-0", segment_id: "s2", scores: { addressed_to_vexa: 0.97, action_item: 0.7 }, words: {} },
};

describe("transcript signals in the rows layout", () => {
  it("puts a chip on the line it belongs to, named for a person", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} layout="rows" marks={marksFor(annotations, undefined)} />);
    const chips = [...container.querySelectorAll("[data-signal-chip]")].map((c) => c.textContent);
    expect(chips).toEqual(["Decision", "To Vexa", "Action"]);
    const first = container.querySelector('[data-signal-chip="decision"]')!;
    expect(first.closest("[data-signal-segment]")?.getAttribute("data-signal-segment")).toBe("s1");
    unmount();
  });

  it("emphasises significant words by weight, not colour alone, and keeps the words intact", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} layout="rows" marks={marksFor(annotations, undefined)} />);
    const strong = [...container.querySelectorAll("[data-signal-word]")];
    expect(strong.map((s) => s.textContent)).toEqual(["proxy", "Friday"]);
    expect(strong.every((s) => s.tagName === "STRONG")).toBe(true);
    expect(container.textContent).toContain("We ship the proxy change on Friday.");
    unmount();
  });

  it("each chip says what it is to a screen reader", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} layout="rows" marks={marksFor(annotations, undefined)} />);
    expect(container.querySelector('[data-signal-chip="addressed_to_vexa"]')?.getAttribute("aria-label")).toBe("Signal: To Vexa");
    unmount();
  });

  it("reads exactly the same without annotations", () => {
    const plain = render(<LiveTranscriptEngine segments={segments} layout="rows" />);
    const empty = render(<LiveTranscriptEngine segments={segments} layout="rows" marks={marksFor({}, undefined)} />);
    expect(empty.container.textContent).toBe(plain.container.textContent);
    expect(empty.container.querySelector("[data-signal-chip],[data-signal-word]")).toBeNull();
    plain.unmount(); empty.unmount();
  });

  it("the search still finds and marks the words on an annotated line", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} layout="rows" query="friday" marks={marksFor(annotations, undefined)} />);
    expect(container.querySelector("[data-transcript-match]")?.textContent).toBe("Friday");
    unmount();
  });
});
