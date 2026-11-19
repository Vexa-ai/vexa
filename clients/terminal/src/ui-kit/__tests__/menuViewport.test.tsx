/** Founder 2026-10-16: the workspace page's "···" menu opened left of its button and the panel's edge
 *  cut it off. A menu renders in a portal (no panel clips it), anchored to its button, and shifts or
 *  flips to stay fully inside the viewport. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Menu } from "../primitives/Menu";

const rect = (x: number, y: number, w: number, h: number) => ({ x, y, left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, toJSON: () => ({}) }) as DOMRect;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("a menu stays inside the viewport", () => {
  it("is portalled to the body and shifted right when it would leave the left edge", () => {
    Object.defineProperty(window, "innerWidth", { value: 1000, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 700, configurable: true });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("vx-menu")) return rect(0, 0, 220, 160);
      if (this.classList.contains("vx-menu-trigger")) return rect(10, 100, 24, 24);
      return rect(0, 0, 0, 0);
    });
    render(<div style={{ overflow: "hidden", width: 50 }}><Menu label="More for Personal" align="end" items={[{ key: "a", label: "Pin to the top of home", onSelect: () => {} }]} /></div>);
    fireEvent.click(screen.getByRole("button", { name: "More for Personal" }));
    const menu = screen.getByRole("menu");
    expect(menu.parentElement).toBe(document.body);
    const left = parseFloat(menu.style.left);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + 220).toBeLessThanOrEqual(1000 - 8);
    expect(parseFloat(menu.style.top)).toBeGreaterThanOrEqual(124);
  });
  it("flips above its button when there is no room below", () => {
    Object.defineProperty(window, "innerWidth", { value: 1000, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 700, configurable: true });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("vx-menu")) return rect(0, 0, 220, 160);
      if (this.classList.contains("vx-menu-trigger")) return rect(500, 650, 24, 24);
      return rect(0, 0, 0, 0);
    });
    render(<Menu label="More" items={[{ key: "a", label: "A", onSelect: () => {} }]} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const top = parseFloat(screen.getByRole("menu").style.top);
    expect(top + 160).toBeLessThanOrEqual(650);
  });
});
