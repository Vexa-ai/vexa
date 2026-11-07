import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { LiveTranscriptEngine } from "../LiveTranscriptEngine";

// Show fast, then reconfirm (#1783): a speaker's drafts awaiting reconfirmation are ALL shown, dimmed,
// in order; when the reconfirmed segment replaces them (same id, the rest withdrawn) only it remains.
function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(ui); });
  return { container, rerender: (u: React.ReactElement) => act(() => root.render(u)), unmount: () => act(() => root.unmount()) };
}

describe("LiveTranscriptEngine — drafts awaiting reconfirmation", () => {
  it("shows every pending draft of the speaker, then only the reconfirmed text", () => {
    const drafts = [
      { id: "ch-0:1:1000", speaker: "Ann", text: "AT&T Bell", completed: false },
      { id: "ch-0:1:2500", speaker: "Ann", text: "and IBM", completed: false },
      { id: "ch-0:1:4000", speaker: "Ann", text: "Watson Labs.", completed: false },
    ];
    const { container, rerender, unmount } = render(<LiveTranscriptEngine segments={drafts} />);
    for (const d of drafts) expect(container.textContent).toContain(d.text);
    rerender(<LiveTranscriptEngine segments={[{ id: "ch-0:1:1000", speaker: "Ann", text: "AT&T Bell Labs and IBM Watson Labs.", completed: true }]} />);
    expect(container.textContent).toContain("AT&T Bell Labs and IBM Watson Labs.");
    expect(container.textContent).not.toContain("…");
    unmount();
  });
});
