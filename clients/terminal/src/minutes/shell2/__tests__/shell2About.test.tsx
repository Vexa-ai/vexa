/** UI-1 step 4 (feature `shell2`): right → left (7h) — "Chat about this" becomes "Resume chat · n"
 *  once a chat about the artifact exists; a deleted chat drops out; the panel hold is read once. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CHAT_ABOUT_EVENT, artifactOf, consumeKeepPanel, rememberAbout, setKnownChats } from "../about";
import { ChatAboutButton } from "../ChatAbout";
import { Connected } from "../Connected";
import { OPEN_SESSION_EVENT } from "../../../platform/events";

afterEach(() => { cleanup(); try { localStorage.removeItem("vexa.about.v1"); } catch { /* */ } setKnownChats([]); });

describe("chat about this (7h)", () => {
  it("names pages and meetings, never the panel's own views", () => {
    expect(artifactOf("product", "roadmap.md", "doc", "Roadmap")?.key).toBe("page:product/roadmap.md");
    expect(artifactOf(undefined, "9", "meeting", "Weekly sync")?.key).toBe("meeting:9");
    expect(artifactOf("product", ".workspace", "doc", "x")).toBeNull();
  });
  it("starts a chat about it, holding the panel; then reads Resume chat · n and reopens the latest", () => {
    const a = artifactOf("product", "roadmap.md", "doc", "Roadmap")!;
    const started = vi.fn(), opened = vi.fn();
    window.addEventListener(CHAT_ABOUT_EVENT, started); window.addEventListener(OPEN_SESSION_EVENT, opened);
    setKnownChats([{ id: "c1", label: "About Roadmap" }]);
    const { rerender } = render(<ChatAboutButton a={a} />);
    fireEvent.click(screen.getByRole("button", { name: "Chat about this" }));
    expect(started).toHaveBeenCalledTimes(1);
    expect(consumeKeepPanel()).toBe(true);
    expect(consumeKeepPanel()).toBe(false);                     // read once
    rememberAbout(a.key, "c1");
    rerender(<ChatAboutButton a={a} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume chat · 1" }));
    expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({ session: "c1" });
    window.removeEventListener(CHAT_ABOUT_EVENT, started); window.removeEventListener(OPEN_SESSION_EVENT, opened);
  });
  it("Connected lists the chats as one clickable row each; a deleted chat drops out", () => {
    const a = artifactOf("product", "roadmap.md", "doc", "Roadmap")!;
    rememberAbout(a.key, "c1"); rememberAbout(a.key, "gone");
    setKnownChats([{ id: "c1", label: "About Roadmap" }]);
    render(<Connected a={a} />);
    const rows = document.querySelectorAll(".vx-s2-connected .vx-row2-main");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("About Roadmap");
  });
});
