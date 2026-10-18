/** ListRow columns, RowActions, AvatarStack and Switch (UI-1, 0.13.4). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { AvatarStack, ListRow, RowActions, RowCell, Switch, registerPersonPhotos } from "..";

afterEach(cleanup);

describe("ListRow + RowCell + RowActions", () => {
  it("keeps the verb in a fixed column and every other act in ⋯; the row's own click is separate", () => {
    const open = vi.fn(), verb = vi.fn();
    render(<ListRow size="md" strong title="Approve the change" detail="Example · Oct 9" onSelect={open} label="Open: Approve the change"
      trailing={<RowActions verb={{ label: "Approve", onClick: verb }} more={[{ key: "decline", label: "Decline", onSelect: () => undefined }]} />} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(verb).toHaveBeenCalledTimes(1);
    expect(open).not.toHaveBeenCalled();
    expect(document.querySelector('.vx-row2-cell[data-w="104"] [data-verb="Approve"]')).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Decline" })).toBeNull();      // not on the row: inside ⋯
    fireEvent.click(screen.getByRole("button", { name: "Open: Approve the change" }));
    expect(open).toHaveBeenCalledTimes(1);
  });
  it("an empty slot keeps its width, so the verb never moves", () => {
    render(<ListRow title="x" trailing={<RowActions />} />);
    expect([...document.querySelectorAll(".vx-row2-cell")].map((e) => e.getAttribute("data-w"))).toEqual(["24", "104", "28"]);
  });
  it("the suggestion's reason opens on a tap, not only on hover", () => {
    render(<ListRow title="x" trailing={<RowActions hint={{ text: "Matches your rule", label: "Why this is suggested" }} />} />);
    fireEvent.click(screen.getByRole("button", { name: "Why this is suggested" }));
    expect(screen.getByRole("dialog", { name: "Why this is suggested" }).textContent).toContain("Matches your rule");
  });
  it("RowCell widths are a closed set", () => {
    render(<RowCell w={72} align="end">10:00</RowCell>);
    expect(document.querySelector('.vx-row2-cell[data-w="72"][data-align="end"]')).toBeTruthy();
  });
  it("R11: hover-revealed actions are not drawn without a hovering pointer", () => {
    const css = readFileSync(resolve(__dirname, "../primitives/controls.css"), "utf8");
    const block = css.slice(css.indexOf("@media (hover: none)"), css.indexOf("@media (hover: none)") + 400);
    expect(block).toContain("[data-overlay-actions] .vx-row2-actions");
    expect(block).toContain("display: none");
  });
});

describe("AvatarStack", () => {
  it("shows at most 3 faces then +N, a registered photo, else the monogram", () => {
    registerPersonPhotos({ "Ana Ortiz": "/api/people/u1/photo" });
    render(<AvatarStack names={["Ana Ortiz", "Ben Kim", "Cleo Lane", "Dan Moss", "Eve Park"]} />);
    const stack = screen.getByRole("img", { name: "Ana Ortiz, Ben Kim, Cleo Lane, Dan Moss, Eve Park" });
    expect(stack.querySelectorAll(".vx-id:not(.vx-avs-more)")).toHaveLength(3);
    expect(stack.textContent).toContain("+2");
    expect(stack.querySelector("img")?.getAttribute("src")).toBe("/api/people/u1/photo");
    expect(stack.querySelectorAll(".vx-id-mono").length).toBe(2);
  });
});

describe("Switch", () => {
  it("is a role=switch named by its label that toggles", () => {
    const on = vi.fn();
    render(<Switch checked={false} onChange={on} label="Vexa joins" />);
    const sw = screen.getByRole("switch", { name: "Vexa joins" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(on).toHaveBeenCalledWith(true);
  });
});
