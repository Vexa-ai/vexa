/** `<Identity>` — the one component for everything that has a face (UI decisions 15f, 15g). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { Identity, IdentityStack } from "../Identity";

afterEach(cleanup);

describe("Identity", () => {
  it("draws the same monogram and colour for the same id on every render", () => {
    const a = render(<Identity kind="workspace" id="k4m9x2q7bd" name="Studio launch" />).container.innerHTML;
    cleanup();
    const b = render(<Identity kind="workspace" id="k4m9x2q7bd" name="Studio launch" />).container.innerHTML;
    expect(a).toBe(b);
    expect(a).toContain("SL");
  });

  it("a workspace asks its own image route and falls back to the monogram when there is none", () => {
    const { container } = render(<Identity kind="workspace" id="w1" slug="garden-x1" name="Garden" />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/workspaces/garden-x1/identity/icon");
    fireEvent.error(img);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("G");
  });

  it("a CRM person who is not a user: initials and the company logo badge, never a face", () => {
    const { container } = render(<Identity kind="person" id="p1" name="Ada Lovelace" user={false}
      avatar="g:0123" logo="d:engines.example.com" />);
    const imgs = [...container.querySelectorAll("img")];
    expect(container.textContent).toContain("AL");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute("src")).toBe("/api/home/media/d%3Aengines.example.com");
    expect(imgs[0].closest("[data-badge]")).not.toBeNull();
  });

  it("a user with a photo shows it", () => {
    const { container } = render(<Identity kind="person" id="u1" name="Jane" user src="/api/people/u1/photo" />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/people/u1/photo");
  });

  it("an agent gets a generated mark", () => {
    const { container } = render(<Identity kind="agent" id="routine-7" name="Morning digest" />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector("[data-kind='agent']")).not.toBeNull();
  });

  it("exposes Change as a button when onChange is given", () => {
    const onChange = vi.fn();
    const { getByRole } = render(<Identity kind="person" id="u1" name="Jane" user onChange={onChange} />);
    fireEvent.click(getByRole("button", { name: /change photo/i }));
    expect(onChange).toHaveBeenCalled();
  });

  it("is decorative by default and labelled when asked", () => {
    const { container } = render(<Identity kind="company" id="c" name="Acme" />);
    expect(container.firstElementChild!.getAttribute("aria-hidden")).toBe("true");
    cleanup();
    const { getByRole } = render(<Identity kind="company" id="c" name="Acme" label />);
    expect(getByRole("img", { name: "Acme" })).toBeTruthy();
  });

  it("sizes by a custom property, never by inline colour", () => {
    const { container } = render(<Identity kind="workspace" id="w" name="W" size={40} />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.getAttribute("style")).toContain("--id-size: 40px");
    expect(el.getAttribute("style")).not.toMatch(/(^|;)\s*(background|color)\s*:/);
  });
});

describe("IdentityStack", () => {
  it("shows at most `max` and counts the rest", () => {
    const people = Array.from({ length: 6 }, (_, i) => ({ kind: "person" as const, id: `p${i}`, name: `P ${i}` }));
    const { container } = render(<IdentityStack items={people} max={4} />);
    expect(container.querySelectorAll("[data-kind='person']")).toHaveLength(4);
    expect(container.textContent).toContain("+2");
  });
});

describe("Avatar (home blocks) — a user's photo comes from the photo route", async () => {
  const { Avatar } = await import("../blocks/Blocks");
  it("prefers the photo route over a media key", () => {
    const { container } = render(<Avatar p={{ name: "Jane", initials: "J", photo: "/api/people/7/photo", avatar: "g:00" }} />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/people/7/photo");
  });
});
