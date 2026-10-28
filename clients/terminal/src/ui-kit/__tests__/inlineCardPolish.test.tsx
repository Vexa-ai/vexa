/** Founder 2026-10-16, "make this nicer": an answered card folds to one line ("Who's it for? → Me and
 *  others") and offers nothing; the free-text Send shows only with text; the card is light, ≤520px,
 *  with padded pill chips. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InlineCard, parseInlineCard } from "../InlineCard";

afterEach(cleanup);
const Q = parseInlineCard({ id: "ws-who", question: "Who's it for?", chips: ["Just me", "Me and others"], free: true })!;

describe("the inline card, polished", () => {
  it("folds to one line with the answer, and offers nothing after", () => {
    render(<InlineCard spec={Q} onAnswer={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Me and others" }));
    const card = document.querySelector("[data-inline-card=ws-who]")!;
    expect(card.hasAttribute("data-answered")).toBe(true);
    expect(card.textContent).toBe("Who's it for? → Me and others");
    expect(card.querySelectorAll("button")).toHaveLength(0);
  });
  it("shows Send only once there is text", () => {
    render(<InlineCard spec={Q} onAnswer={() => {}} />);
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });
  it("is light and its chips are padded pills", () => {
    const css = readFileSync(join(__dirname, "../layout/layout.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = (sel: string) => new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? "";
    expect(rule(".vx-icard")).toMatch(/max-width\s*:\s*520px/);
    expect(rule(".vx-icard-chip")).toMatch(/padding\s*:\s*6px 12px/);
    expect(rule(".vx-icard-chip")).toMatch(/border-radius\s*:\s*999px/);
    expect(rule(".vx-icard-chips")).toMatch(/gap\s*:\s*8px/);
  });
});
