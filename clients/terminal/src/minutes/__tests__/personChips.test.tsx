/** UI 7mm (founder 2026-10-18): every person chip and avatar opens the person's profile — the meeting
 *  panel's people, room members, a room of two's header, call avatars — with a hover card carrying
 *  Open profile and Copy reference. A person badge in text opens the same profile. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const opened: { path: string; label?: string }[] = [];
vi.mock("../desktop/home2/acts", () => ({ openDocOnRight: (_slug: string, path: string, label?: string) => { opened.push({ path, label }); } }));
const copied: unknown[] = [];
vi.mock("../../surfaces/reference", async (orig) => ({ ...(await orig<object>()), copyReference: async (t: unknown) => { copied.push(t); return true; } }));

import { PersonChip, personTarget } from "../shell2/PersonChip";
import { MeetingViewHeader } from "../../canvas/MeetingViewHeader";

afterEach(() => { cleanup(); opened.length = 0; copied.length = 0; vi.useRealTimers(); });

describe("a person chip opens the person", () => {
  it("a click opens their profile, by account", () => {
    render(<PersonChip person={{ name: "Maya Lind", subject: "u7" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Maya Lind" }));
    expect(opened).toEqual([{ path: ".person/u%3Au7", label: "Maya Lind" }]);
  });

  it("the hover card offers Open profile and Copy reference", () => {
    vi.useFakeTimers();
    const { container } = render(<PersonChip person={{ name: "Maya Lind", email: "maya@example.com" }} />);
    fireEvent.mouseEnter(container.querySelector("[data-person-chip]")!);
    act(() => { vi.advanceTimersByTime(400); });
    expect(screen.getByRole("dialog", { name: "Maya Lind" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy reference" }));
    expect(copied).toEqual([{ kind: "person", id: "maya@example.com", label: "Maya Lind" }]);
    fireEvent.click(screen.getByRole("button", { name: "Open profile" }));
    expect(opened.at(-1)?.path).toBe(".person/e%3Amaya%40example.com");
  });

  it("a name with no id has no reference to copy, and still opens a profile", () => {
    expect(personTarget({ name: "Someone" })).toBeNull();
    expect(personTarget({ name: "Maya", subject: "u7" })).toEqual({ kind: "person", id: "u7", label: "Maya" });
  });

  it("the meeting panel's people are clickable", () => {
    const m = { id: "202", title: "Call with Vexa", when: "", status: "past", platform: "google_meet",
      participants: [{ name: "Bruno Alves", role: "" }], attendees: [] } as never;
    render(<MeetingViewHeader meeting={m} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Bruno Alves" }));
    expect(opened.at(-1)?.label).toBe("Bruno Alves");
  });
});
