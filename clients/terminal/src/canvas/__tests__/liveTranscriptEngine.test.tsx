import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { LiveTranscriptEngine, type EngineEntity } from "../LiveTranscriptEngine";

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(ui); });
  return { container, unmount: () => act(() => root.unmount()) };
}

const segments = [{ id: "s0", speaker: "Jane", text: "I spoke with Acme today.", completed: true }];

describe("LiveTranscriptEngine — processed v2 inline rendering", () => {
  it("RAW mode (no entities) renders plain text with no entity spans/menu", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} />);
    expect(container.textContent).toContain("I spoke with Acme today.");
    expect(container.querySelector('[role="button"][aria-haspopup="menu"]')).toBeNull();
    unmount();
  });

  it("PROCESSED mode highlights the entity inline and fires Research on click", () => {
    const research = vi.fn();
    const entities: EngineEntity[] = [{ id: "c1", label: "Acme", kind: "company", docPath: "kg/acme.md" }];
    const { container, unmount } = render(
      <LiveTranscriptEngine segments={segments} entities={entities} actions={{ research }} />,
    );
    const mention = container.querySelector('[role="button"][aria-haspopup="menu"]') as HTMLElement;
    expect(mention).not.toBeNull();
    expect(mention.textContent).toBe("Acme");

    act(() => { mention.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const items = [...container.querySelectorAll('[role="menuitem"]')];
    const researchItem = items.find((el) => el.textContent === "Research") as HTMLElement;
    expect(researchItem).toBeDefined();

    act(() => { researchItem.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(research).toHaveBeenCalledWith({ id: "c1", name: "Acme", kind: "company", docPath: "kg/acme.md" });
    unmount();
  });

  it("renders actionable signal badges that fire onSignal", () => {
    const onSignal = vi.fn();
    const { container, unmount } = render(
      <LiveTranscriptEngine
        segments={segments}
        entities={[]}
        signals={[{ id: "sig1", kind: "decision", label: "Ship Friday" }]}
        actions={{ onSignal }}
      />,
    );
    const badge = container.querySelector('[aria-label="signals"] button') as HTMLElement;
    expect(badge).not.toBeNull();
    act(() => { badge.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(onSignal).toHaveBeenCalledWith({ id: "sig1", kind: "decision", label: "Ship Friday" });
    unmount();
  });
});
