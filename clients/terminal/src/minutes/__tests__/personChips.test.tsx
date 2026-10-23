/** UI 7mm (founder 2026-10-18: "those guys are unclickable"): a person is a live reference — the
 *  meeting panel's people, room members, a room of two, message authors, a notice's actor, call
 *  avatars, Home's live call. A click opens their profile; a hover shows the entity hover card. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const opened: { path: string; label?: string }[] = [];
vi.mock("../desktop/home2/acts", () => ({ openDocOnRight: (_slug: string, path: string, label?: string) => { opened.push({ path, label }); } }));

import { PersonChip, personTarget, resetPersonPeers } from "../shell2/PersonChip";
import { MeetingViewHeader } from "../../canvas/MeetingViewHeader";
import { NoticeText } from "../NotificationBell";
import { resetHoverCache } from "../../ui-kit/hoverCard";
import { providePersonRef } from "../../ui-kit/shellProvided";
import { PersonRefSlot } from "../../ui-kit/PersonRefSlot";

const realFetch = globalThis.fetch;
beforeEach(() => {
  resetPersonPeers(); resetHoverCache();
  globalThis.fetch = vi.fn(async (url: string) => {
    if (String(url).startsWith("/api/people/peers")) return new Response(JSON.stringify({ people: [{ subject: "u9", name: "Bruno Alves" }] }));
    if (String(url).startsWith("/api/people/u7/profile")) return new Response(JSON.stringify({ profile: { headline: "Head of Ops", company: "Acme" } }));
    return new Response("{}", { status: 404 });
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); opened.length = 0; globalThis.fetch = realFetch; vi.useRealTimers(); });

describe("a person chip opens the person", () => {
  it("a click opens their profile, by account", async () => {
    render(<PersonChip person={{ name: "Maya Lind", subject: "u7" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Maya Lind" }));
    await waitFor(() => expect(opened).toEqual([{ path: ".person/u%3Au7", label: "Maya Lind" }]));
  });

  it("a name alone is resolved to their account through the people you share workspaces with", async () => {
    render(<PersonChip person={{ name: "Bruno Alves" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Bruno Alves" }));
    await waitFor(() => expect(opened.at(-1)?.path).toBe(".person/u%3Au9"));
  });

  it("the hover card says who they are", async () => {
    render(<PersonChip person={{ name: "Maya Lind", subject: "u7" }} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: "Open Maya Lind" }));
    expect(await screen.findByText("Head of Ops · Acme", {}, { timeout: 2000 })).toBeTruthy();
  });

  it("only a person with an id has a reference to copy or drag", () => {
    expect(personTarget({ name: "Someone" })).toBeNull();
    expect(personTarget({ name: "Maya", subject: "u7" })).toEqual({ kind: "person", id: "u7", label: "Maya" });
  });

  it("the meeting panel's people are clickable", async () => {
    const m = { id: "202", title: "Call with Vexa", when: "", status: "past", platform: "google_meet",
      participants: [{ name: "Bruno Alves", role: "" }], attendees: [] } as never;
    render(<MeetingViewHeader meeting={m} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Bruno Alves" }));
    await waitFor(() => expect(opened.at(-1)?.label).toBe("Bruno Alves"));
  });

  it("a notice's actor is a live reference", async () => {
    render(<NoticeText n={{ id: "n1", kind: "chat_shared", text: "Maya Lind added you to Board prep", created: 0, read: false,
      actor: { id: "u7", name: "Maya Lind" }, object: { kind: "chat", id: "c1" } }} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Maya Lind" }));
    await waitFor(() => expect(opened.at(-1)?.path).toBe(".person/u%3Au7"));
    expect(screen.getByText(/added you to Board prep/)).toBeTruthy();
  });

  it("a message author (the chat, below the shell) is the same live reference, supplied by the shell", async () => {
    render(<PersonRefSlot person={{ name: "Maya Lind" }} />);
    expect(screen.queryByRole("button")).toBeNull();          // nothing supplied: the name as it is
    cleanup();
    providePersonRef(PersonChip);
    render(<PersonRefSlot person={{ name: "Bruno Alves" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Bruno Alves" }));
    await waitFor(() => expect(opened.at(-1)?.path).toBe(".person/u%3Au9"));
    providePersonRef(null);
  });
});
