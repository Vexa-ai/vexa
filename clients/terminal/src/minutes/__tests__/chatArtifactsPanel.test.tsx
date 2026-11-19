/** The panel with nothing open is this chat's artifacts, not a workspace — "All files" is one click deeper. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import type { ChatArtifact } from "../chatArtifacts";

afterEach(() => cleanup());

const at = new Date(2026, 9, 15, 10, 12).getTime();
const ITEMS: ChatArtifact[] = [
  { key: "w:/w/ait/sponsor-plan.md", kind: "wrote", title: "Sponsor plan", at, file: "/w/ait/sponsor-plan.md" },
  { key: "m:120", kind: "meeting", title: "Tatiana", meeting: "120" },
];

describe("nothing open → this chat's artifacts", () => {
  it("lists them one line each, opens one on click, and offers All files", () => {
    const onArtifact = vi.fn();
    const c = render(<PagesPanel pages={[]} docPath="" onOpen={() => {}} body={null} artifacts={ITEMS} onArtifact={onArtifact} />).container;
    const rows = [...c.querySelectorAll("[data-entry=artifact]")].map((r) => r.textContent);
    expect(rows).toEqual(["Wrote Sponsor plan10:12", "Meeting with Tatiana"]);
    expect(c.querySelector("[data-crumb]")).toBeNull();            // no workspace address, no tree
    expect(c.textContent).not.toContain("No page here yet");
    fireEvent.click(c.querySelector("[data-entry=artifact]")!);
    expect(onArtifact).toHaveBeenCalledWith(ITEMS[0]);
    expect(c.querySelector("[data-entry=all-files]")?.textContent).toBe("All files");
  });

  it("an empty chat says so instead of borrowing a workspace", () => {
    const c = render(<PagesPanel pages={[]} docPath="" onOpen={() => {}} body={null} artifacts={[]} />).container;
    expect(c.querySelectorAll("[data-entry=artifact]").length).toBe(0);
    expect(c.textContent).toContain("Nothing from this chat yet");
  });

  it("a page in front is still the page", () => {
    const c = render(<PagesPanel pages={[{ path: "plan.md", label: "plan" }]} docPath="plan.md" onOpen={() => {}} body={"# Plan\n"} artifacts={ITEMS} />).container;
    expect(c.querySelector("[data-chat-artifacts]")).toBeNull();
  });
});
