import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { isLongPaste, messageParts, withPastes, PasteChip, UserMessage, PASTE_OPEN } from "../pastedText";

// LONG PASTES COLLAPSE (#1783) — founder, on a whole web page pasted into the composer that came
// back as a giant user bubble.
afterEach(cleanup);
const page = Array.from({ length: 214 }, (_, i) => `line ${i + 1} of the page`).join("\n");

describe("what counts as long", () => {
  it("over 15 lines or 1,500 characters", () => {
    expect(isLongPaste("a\nb\nc")).toBe(false);
    expect(isLongPaste(Array(16).fill("x").join("\n"))).toBe(true);
    expect(isLongPaste("x".repeat(1501))).toBe(true);
  });
});

describe("the message", () => {
  it("the agent gets the typed words and every paste whole", () => {
    const sent = withPastes("summarise this", [page]);
    expect(sent.startsWith("summarise this\n\n" + PASTE_OPEN)).toBe(true);
    expect(sent).toContain("line 214 of the page");
  });
  it("the bubble shows the typed words and one collapsed chip", () => {
    const parts = messageParts(withPastes("summarise this", [page]));
    expect(parts.map((p) => p.kind)).toEqual(["text", "paste"]);
    expect(parts[0]).toEqual({ kind: "text", text: "summarise this" });
    expect(parts[1].text).toBe(page);
  });
  it("a long stored message with no markers collapses whole (history after a reload)", () => {
    expect(messageParts(page)).toEqual([{ kind: "paste", text: page, label: "Long message" }]);
    expect(messageParts("hello")).toEqual([{ kind: "text", text: "hello" }]);
  });
});

describe("the chip", () => {
  it("names its size and first line, and opens on click", () => {
    render(<UserMessage text={withPastes("summarise this", [page])} renderText={(t) => <span>{t}</span>} />);
    expect(screen.getByText("summarise this")).toBeTruthy();
    expect(screen.getByText("Pasted text · 214 lines")).toBeTruthy();
    expect(screen.queryByText(/line 200 of the page/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText(/line 200 of the page/)).toBeTruthy();
  });
  it("in the composer: × removes it, a click hands it back for editing", () => {
    const remove = vi.fn(), edit = vi.fn();
    render(<PasteChip text={page} onRemove={remove} onEdit={edit} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove pasted text" }));
    expect(remove).toHaveBeenCalled();
    fireEvent.click(screen.getByTitle("Edit the pasted text"));
    expect(edit).toHaveBeenCalled();
  });
});
