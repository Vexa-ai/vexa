/** PREPARE FROM HOME (`home_prepare`; founder 2026-10-18: "why is that not in the home tab ready to
 *  start prep with chat and meeting from one click?"). Claims:
 *    · the next planned meeting (meeting-api's row) heads the strip: "<title> · in 30 min" within the
 *      hour, "<day time> · <title>" later; nothing when none is planned this week, never a live or
 *      past one;
 *    · its ticks are the Prep section's own reads (goal on the page, people known, material);
 *    · ONE CLICK: the meeting opens on the right, then its conversation in the centre — a new chat
 *      about the meeting carrying its full reference, or the chat already about it with the reference
 *      drafted into that chat — never a bare title. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent } from "@testing-library/react";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const s = vi.hoisted(() => ({ rows: [] as unknown[], body: "" as string, people: [] as unknown[] }));
vi.mock("../../surfaces/liveMeetings", async (orig) => ({ ...(await orig<typeof import("../../surfaces/liveMeetings")>()), useLiveMeetings: () => s.rows }));
vi.mock("../meetingNote", async (orig) => ({ ...(await orig<typeof import("../meetingNote")>()),
  fetchMeetingNote: vi.fn(async () => (s.body ? { path: "kg/entities/meeting/x.md", workspace: "", title: "", transcript: "", cursor: "" } : { path: null, workspace: "", title: "", transcript: "", cursor: "" })),
}));
vi.mock("../../data/workspaceApi", async (orig) => ({ ...(await orig<typeof import("../../data/workspaceApi")>()), readWorkspaceFile: vi.fn(async () => s.body) }));
vi.mock("../shell2/PeoplePage", async (orig) => ({ ...(await orig<typeof import("../shell2/PeoplePage")>()), usePeople: () => ({ phase: "ready", people: s.people }) }));

import { HomePrepare, nextPlanned, prepareFromHome } from "../desktop/home2/HomePrepare";
import { ASK_CHAT_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { CHAT_ABOUT_EVENT, rememberAbout, setKnownChats } from "../shell2/about";

const NOW = Date.UTC(2026, 9, 18, 12, 0);
const iso = (ms: number) => new Date(ms).toISOString();
const row = (id: string, title: string, at: number, live_status = "scheduled", over: Record<string, unknown> = {}) =>
  ({ id, title, scheduled_at: iso(at), live_status, status: "past", when: "", platform: "Google Meet", participants: [], mentioned: [], actions: [], ...over });

let container: HTMLDivElement;
let root: Root;
const events: { name: string; detail: unknown }[] = [];
const NAMES = [OPEN_MEETING_EVENT, OPEN_SESSION_EVENT, ASK_CHAT_EVENT, CHAT_ABOUT_EVENT, "vexa:terminal:focus-chat"];
const on = (name: string) => (e: Event) => events.push({ name, detail: (e as CustomEvent).detail });
const handlers = NAMES.map((n) => [n, on(n)] as const);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout"] });
  s.rows = []; s.body = ""; s.people = []; events.length = 0;
  try { localStorage.clear(); } catch { /* none */ }
  handlers.forEach(([n, h]) => window.addEventListener(n, h));
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount()); container.remove();
  handlers.forEach(([n, h]) => window.removeEventListener(n, h));
  vi.useRealTimers();
});

describe("the next planned meeting", () => {
  it("is the earliest scheduled one ahead within the week — never live, past or far off", () => {
    const rows = [
      row("1", "Live one", NOW - 60_000, "active"),
      row("2", "Past", NOW - 3600_000, "completed"),
      row("3", "Later", NOW + 3 * 3600_000),
      row("4", "Weekly sync", NOW + 30 * 60_000),
      row("5", "Next month", NOW + 30 * 86_400_000),
    ] as never[];
    expect(nextPlanned(rows, NOW)?.id).toBe("4");
    expect(nextPlanned([rows[0], rows[1], rows[4]], NOW)).toBeNull();
  });

  it("heads the strip with its time first or last, and the Prep ticks", async () => {
    s.rows = [row("201", "Weekly sync", NOW + 30 * 60_000, "scheduled", { attendees: [{ email: "ana@x.example" }] })];
    s.people = [{ key: "a", subject: "7", name: "Ana", email: "ana@x.example", how: ["met"], workspaces: [], roles: {}, vexa: true, last: null, page: null, presence: null }];
    s.body = "---\ngoals: Agree the date\n---\n# Weekly sync\n";
    await act(async () => { root.render(<HomePrepare now={NOW} />); });
    const el = container.querySelector('[data-home-prepare="soon"]');
    expect(el?.textContent).toContain("Weekly sync · in 30 min");
    expect(el?.textContent).toContain("Prepare");
    expect(container.querySelector('[data-prep-tick="goal"]')?.hasAttribute("data-ok")).toBe(true);
    expect(container.querySelector('[data-prep-tick="people"]')?.hasAttribute("data-ok")).toBe(true);
    expect(container.querySelector('[data-prep-tick="material"]')?.hasAttribute("data-ok")).toBe(false);

    s.rows = [row("202", "Board review", NOW + 3 * 86_400_000)];
    await act(async () => { root.render(<HomePrepare now={NOW + 1} />); });
    const later = container.querySelector('[data-home-prepare="later"]');
    expect(later?.textContent).toMatch(/\d{2}:\d{2} · Board review/);
  });

  it("draws nothing when nothing is planned", async () => {
    await act(async () => { root.render(<HomePrepare now={NOW} />); });
    expect(container.innerHTML).toBe("");
  });
});

describe("one click on Prepare", () => {
  it("opens the meeting on the right, then a new chat about it with its full reference", async () => {
    s.rows = [row("201", "Weekly sync", NOW + 30 * 60_000, "scheduled", { workspace_id: "pilot" })];
    await act(async () => { root.render(<HomePrepare now={NOW} />); });
    const verb = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Prepare"))!;
    await act(async () => { fireEvent.click(verb); });
    await act(async () => { vi.runAllTimers(); });
    expect(events[0]).toEqual({ name: OPEN_MEETING_EVENT, detail: { ref: "201" } });
    const about = events.find((e) => e.name === CHAT_ABOUT_EVENT)!;
    expect(about.detail).toMatchObject({ key: "meeting:201", kind: "meeting", slug: "pilot", ref: "[[meeting:201]]" });
    expect(events.some((e) => e.name === OPEN_SESSION_EVENT)).toBe(false);
  });

  it("resumes the chat already about the meeting and drafts the reference into THAT chat", async () => {
    setKnownChats([{ id: "s-prep" }]);
    rememberAbout("meeting:201", "s-prep");
    prepareFromHome({ id: "201", title: "Weekly sync", workspace_id: undefined });
    await act(async () => { vi.runAllTimers(); });
    // the reference is offered as the resumed chat mounts (a draft for an unmounted chat is dropped);
    // repeats are harmless — the composer keeps one badge per reference
    expect(events.slice(0, 4).map((e) => e.name)).toEqual([OPEN_MEETING_EVENT, OPEN_SESSION_EVENT, ASK_CHAT_EVENT, "vexa:terminal:focus-chat"]);
    expect(events.filter((e) => e.name === ASK_CHAT_EVENT)).toHaveLength(3);
    expect(events[1].detail).toEqual({ session: "s-prep" });
    expect(events[2].detail).toEqual({ mode: "draft", session: "s-prep", prompt: "[[meeting:201]] " });
    // never a bare title in the composer
    expect(JSON.stringify(events[2].detail)).not.toContain("Weekly sync");
  });
});
