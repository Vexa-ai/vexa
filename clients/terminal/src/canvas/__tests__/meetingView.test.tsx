/** THE ONE MEETING VIEW (founder-approved design, 2026-10-11) — driven through the view itself,
 *  because every claim that matters is about the screen:
 *
 *    · it renders fully WITH notes and WITHOUT them — the meeting never depends on the file;
 *    · Create and Update call the routes that already exist (the mint, and the Extend act on the
 *      meeting's page) — nothing new writes the notes;
 *    · the notes never show the transcript a second time (an old page's slot is dropped on render,
 *      and the file is not touched);
 *    · the transcript is collapsed to its tail by default — live AND finished (founder correction,
 *      2026-10-11: the live tail keeps updating, expanding is the person's choice) — and expanded on
 *      the person's toggle, remembered per meeting for the session;
 *    · nothing on it says "Brief" or "Minutes".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent } from "@testing-library/react";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let meetingsState: unknown[] = [];
let liveState: Record<string, unknown> = {};
const durableState: { lines: unknown[] } = { lines: [] };
const EMPTY_LIVE = { transcript: [], issues: [], connected: false, ended: false, reconnects: 0 };

vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => meetingsState,
  useLiveMeetingsConnection: () => true,
  refreshMeetings: vi.fn(),
  fetchDurableTranscript: vi.fn(async () => ({ lines: durableState.lines })),
}));
vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => ({ ...EMPTY_LIVE, ...liveState }),
}));

import { MeetingView } from "../MeetingView";
import { ASK_CHAT_EVENT, ServicesProvider, WORKSPACE_COMMIT_EVENT, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import { clearPending, pendingLanding } from "../../minutes/extend";
import { resetActs } from "../../surfaces/actState";
import { notesForView, notesFreshness, transcriptTail, withoutEmptySections } from "../meetingNotes";
import type { ChatIntent } from "../../surfaces/chatIntent";

const ROW = "147";
const NOTE = "kg/entities/meeting/2026-10-11-1400-design-review.md";
const AT = Date.UTC(2026, 9, 11, 14, 0, 0);
const NOTES_MD = [
  "---", "type: meeting", `meeting: ${ROW}`, "title: Design review", "transcript_cursor: 4", "---", "",
  "# Design review", "", `<!-- vexa:transcript meeting=${ROW} -->`, "", "## Decisions", "", "- Ship the one view on Friday", "",
].join("\n");

const LINES = ["first we agree the scope", "then the header", "notes go on top", "transcript below it", "collapsed by default"];
const live = (n = LINES.length) => LINES.slice(0, n).map((text, i) => ({ id: `s${i}`, speaker: i % 2 ? "Ravi" : "Jane", text, t: i * 15, tsMs: AT + i * 15000, completed: true }));

const row = (over: Record<string, unknown> = {}) => ({
  id: ROW, native_id: "abc-defg-hij", session_uid: "uid-1", title: "Design review", when: "", status: "live", live_status: "active",
  platform: "Google Meet", start_time: new Date(AT).toISOString(), participants: [{ name: "Jane" }, { name: "Ravi" }],
  mentioned: [], actions: [], transcript: [], insights: [], ...over,
});

let notePath: string | null = NOTE;
let fileBody: string | null = NOTES_MD;
const calls: { method: string; url: string; body?: string }[] = [];
const asks: { prompt?: string; display?: string; intent?: ChatIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  notePath = NOTE; fileBody = NOTES_MD; calls.length = 0; asks.length = 0;
  liveState = {}; durableState.lines = [];
  try { sessionStorage.clear(); } catch { /* */ }
  clearPending(); resetActs();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.startsWith("/api/meeting/note") && method === "POST") {
      notePath = NOTE; fileBody = NOTES_MD.replace("transcript_cursor: 4", "transcript_cursor:");
      return json({ path: NOTE, created: true });
    }
    if (url.startsWith("/api/meeting/note")) return json({ path: notePath, transcript: notePath ? ROW : "", cursor: notePath ? "4" : "" });
    if (url.startsWith("/api/workspace/file")) return fileBody === null ? json({ detail: "no" }, 404) : json({ content: fileBody });
    if (url.startsWith("/api/workspaces/personal/git/history")) return json({ slug: "personal", branch: "main", path: NOTE, limit: 1, commits: [{ sha: "a", msg: "m", when: "", ts: Math.floor(AT / 1000) + 3600 }] });
    return json({});
  }));
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
});

afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  act(() => { root?.unmount(); });
  container.remove();
  vi.unstubAllGlobals();
});

async function flush() { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); }

async function renderView(over: Record<string, unknown> = {}, segments = live()) {
  meetingsState = [row(over)];
  liveState = { transcript: segments };
  root = createRoot(container);
  const services = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  await act(async () => {
    root.render(<ServicesProvider container={services}><MeetingView meetingId={ROW} /></ServicesProvider>);
  });
  await flush();
}

const q = (sel: string) => container.querySelector(sel) as HTMLElement | null;
const transcriptText = () => q("[data-meeting-transcript]")?.textContent ?? "";

describe("the view renders with notes", () => {
  it("shows the header, the notes (labelled Notes + the file path) and the transcript under them", async () => {
    await renderView();
    expect(q("[data-doc-name]")?.textContent).toBe("Design review");
    expect(q("[data-meeting-metadata]")?.textContent).toMatch(/Google Meet .* 2 people · live · \d+ min/);
    expect(q("#notes-147")?.textContent).toBe("Notes");
    expect(q("[data-notes-path]")?.textContent).toBe(NOTE);
    expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
    expect(q("[data-notes-freshness]")?.textContent).toMatch(/^Updated .+ · from transcript up to 0:45$/);
    // notes on top, transcript below
    const order = [...container.querySelectorAll("[data-meeting-notes],[data-meeting-transcript]")].map((e) => e.hasAttribute("data-meeting-notes") ? "notes" : "transcript");
    expect(order).toEqual(["notes", "transcript"]);
  });

  it("never shows the transcript twice — an old page's slot is dropped from the notes, the file untouched", async () => {
    await renderView();
    expect(q("[data-notes-body] [data-transcript-widget]")).toBeNull();
    expect(container.querySelectorAll("[data-transcript-widget]")).toHaveLength(0);
    expect(calls.some((c) => c.method !== "GET" && c.url.startsWith("/api/workspace/file"))).toBe(false);
  });

  it("Update notes posts the EXISTING Extend act on the meeting's page — and the view stays put", async () => {
    await renderView();
    const btn = q('[data-notes-act="update"]')!;
    expect(btn.textContent).toContain("Update notes");
    await act(async () => { fireEvent.click(btn); });
    await flush();
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toMatchObject({ kind: "extend", path: NOTE, meeting: ROW });
    // the landing that would navigate the panel to the file is cleared: the notes are already here
    expect(pendingLanding()).toBeNull();
    // no other writer: nothing on this view wrote the file or minted a page
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    // while the act runs the control says so instead of offering a second press
    expect(q('[data-notes-act="update"]')?.getAttribute("role")).toBe("status");
  });

  it("re-reads the notes when a turn commits — an update (or a chat edit) shows up without a reload", async () => {
    await renderView();
    fileBody = NOTES_MD.replace("Ship the one view on Friday", "Ship it on Monday");
    await act(async () => { window.dispatchEvent(new Event(WORKSPACE_COMMIT_EVENT)); });
    await flush();
    expect(q("[data-notes-body]")?.textContent).toContain("Ship it on Monday");
  });
});

describe("the view renders without notes", () => {
  it("same layout, a dashed placeholder and Create notes — the meeting opens without the file", async () => {
    notePath = null;
    await renderView();
    expect(q("[data-doc-name]")?.textContent).toBe("Design review");
    expect(q("[data-notes-empty]")?.textContent).toContain("No notes for this meeting yet");
    expect(q('[data-notes-act="create"]')?.textContent).toContain("Create notes");
    expect(q("[data-meeting-transcript]")).not.toBeNull();
    expect(q('[data-notes-act="update"]')).toBeNull();
  });

  it("a path the server knows but this desk does not have is no notes either", async () => {
    fileBody = null;
    await renderView();
    expect(q("[data-notes-empty]")).not.toBeNull();
  });

  it("Create notes mints through POST /api/meeting/note, renders the page, then runs the same Update", async () => {
    notePath = null;
    await renderView();
    await act(async () => { fireEvent.click(q('[data-notes-act="create"]')!); });
    await flush();
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toEqual([{ method: "POST", url: "/api/meeting/note", body: JSON.stringify({ meeting_id: ROW }) }]);
    expect(q("[data-notes-body]")?.textContent).toContain("Design review");
    expect(asks.map((a) => a.intent)).toEqual([expect.objectContaining({ kind: "extend", path: NOTE, meeting: ROW })]);
  });

  it("Create notes with nothing said yet mints the page and asks the agent nothing", async () => {
    notePath = null;
    await renderView({}, []);
    await act(async () => { fireEvent.click(q('[data-notes-act="create"]')!); });
    await flush();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(asks).toHaveLength(0);
  });

  it("a refused create says so and leaves the placeholder", async () => {
    notePath = null;
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/meeting/note") && init?.method === "POST") return json({ detail: "not authorized for this meeting" }, 403);
      if (url.startsWith("/api/meeting/note")) return json({ path: null });
      return json({});
    });
    await renderView();
    await act(async () => { fireEvent.click(q('[data-notes-act="create"]')!); });
    await flush();
    expect(q('[role="alert"]')?.textContent).toBe("You can't create notes for this meeting.");
    expect(q("[data-notes-empty]")).not.toBeNull();
    expect(asks).toHaveLength(0);
  });
});

// jsdom has no layout: the transcript window gets a fixed geometry here, and a played line a fixed
// place above the window's bottom, so following can be measured.
const geom = { scrollHeight: 1000, clientHeight: 128, activeTop: -600 };
const tops = new WeakMap<Element, number>();
const isWin = (el: Element) => el.hasAttribute("data-transcript-window");
const saved: Record<string, PropertyDescriptor | undefined> = {};
function installLayout() {
  for (const k of ["scrollHeight", "clientHeight", "scrollTop"]) saved[k] = Object.getOwnPropertyDescriptor(Element.prototype, k);
  saved.rect = Object.getOwnPropertyDescriptor(Element.prototype, "getBoundingClientRect");
  Object.defineProperty(Element.prototype, "scrollHeight", { configurable: true, get() { return isWin(this) ? geom.scrollHeight : 0; } });
  Object.defineProperty(Element.prototype, "clientHeight", { configurable: true, get() { return isWin(this) ? geom.clientHeight : 0; } });
  Object.defineProperty(Element.prototype, "scrollTop", { configurable: true,
    get() { return tops.get(this) ?? 0; },
    set(v: number) { tops.set(this, isWin(this) ? Math.max(0, Math.min(v, geom.scrollHeight - geom.clientHeight)) : 0); } });
  Object.defineProperty(Element.prototype, "getBoundingClientRect", { configurable: true, value() {
    const top = this.hasAttribute("data-playback-active") ? geom.activeTop : 0;
    return { top, height: 20, bottom: top + 20, left: 0, right: 0, width: 0, x: 0, y: top, toJSON() {} } as DOMRect;
  } });
}
function removeLayout() {
  for (const [k, d] of Object.entries(saved)) {
    const name = k === "rect" ? "getBoundingClientRect" : k;
    if (d) Object.defineProperty(Element.prototype, name, d); else delete (Element.prototype as unknown as Record<string, unknown>)[name];
  }
}
const win = () => q("[data-transcript-window]")!;
const userScroll = async (top: number) => { await act(async () => { win().scrollTop = top; win().dispatchEvent(new Event("scroll")); }); };

describe("the transcript — a scrolling window, or everything", () => {
  beforeEach(installLayout);
  afterEach(removeLayout);

  it("collapsed is a fixed-height window that scrolls through the WHOLE transcript, sitting on the newest line", async () => {
    await renderView();
    const toggle = q("[data-transcript-toggle]")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toBe("Show all");
    expect(q("[data-transcript-count]")?.textContent).toBe("5 lines");
    expect(win().style.maxHeight).toBe("128px");
    expect(win().style.overflowY).toBe("auto");
    expect(win().getAttribute("tabindex")).toBe("0");
    for (const l of LINES) expect(transcriptText()).toContain(l);   // scroll up to any of them
    expect(win().scrollTop).toBe(872);                               // at the bottom
    expect(q("[data-transcript-fade]")).not.toBeNull();              // more above
    expect(q("[data-transcript-live]")?.textContent).toBe("live");
  });

  it("live: new lines keep it on the bottom; a person who scrolls up is left there with Jump to latest", async () => {
    await renderView({}, live(3));
    expect(win().scrollTop).toBe(872);
    await userScroll(100);
    expect(q("[data-transcript-jump]")?.textContent).toBe("Jump to latest");
    liveState = { transcript: live(5) };
    await act(async () => { root.render(<ServicesProvider container={createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))])}><MeetingView meetingId={ROW} /></ServicesProvider>); });
    await flush();
    expect(win().scrollTop).toBe(100);                               // not dragged down
    await act(async () => { fireEvent.click(q("[data-transcript-jump]")!); });
    expect(win().scrollTop).toBe(872);
    expect(q("[data-transcript-jump]")).toBeNull();
  });

  it("a FINISHED meeting starts collapsed too, on its last lines", async () => {
    durableState.lines = LINES.map((text, i) => ({ speaker: "Jane", text, offsetSeconds: i * 15 }));
    await renderView({ status: "past", live_status: "completed", session_uid: undefined }, []);
    expect(q("[data-transcript-toggle]")?.getAttribute("aria-expanded")).toBe("false");
    expect(q("[data-transcript-live]")).toBeNull();
    expect(win().scrollTop).toBe(872);
  });

  it("playback: the played line is highlighted and kept in view; a manual scroll stops following until Follow playback", async () => {
    await renderView({}, live());
    const { registerPlayback } = await import("../../minutes/meetingPlayback");
    const seek = vi.fn();
    const player = registerPlayback(ROW, { seek, pause: vi.fn(), originMs: AT });
    try {
      await act(async () => { player.update(31, true); });            // inside the third line (30s)
      const active = q("[data-playback-active]")!;
      expect(active.textContent).toContain("notes go on top");
      // centred: 872 (bottom) + (-600) line offset, less half the window
      expect(win().scrollTop).toBe(872 - 600 - (128 - 20) / 2);
      const followed = win().scrollTop;
      await userScroll(500);
      expect(q("[data-transcript-follow-playback]")?.textContent).toBe("Follow playback");
      await act(async () => { player.update(32); });
      expect(win().scrollTop).toBe(500);                             // the person's place is kept
      await act(async () => { fireEvent.click(q("[data-transcript-follow-playback]")!); });
      expect(win().scrollTop).not.toBe(500);
      expect(q("[data-transcript-follow-playback]")).toBeNull();
      expect(followed).toBeGreaterThan(0);
      // clicking a line still plays from it
      await act(async () => { fireEvent.click(q("[data-playback-active]")!); });
      expect(seek).toHaveBeenCalled();
    } finally { await act(async () => { player.dispose(); }); }
  });

  it("Show all expands to full height with Copy and Highlight; the choice is remembered for this meeting", async () => {
    await renderView();
    await act(async () => { fireEvent.click(q("[data-transcript-toggle]")!); });
    expect(q("[data-transcript-toggle]")?.getAttribute("aria-expanded")).toBe("true");
    expect(q("[data-transcript-toggle]")?.textContent).toBe("Collapse");
    expect(win().style.maxHeight).toBe("");
    expect(q("[data-transcript-fade]")).toBeNull();
    expect(q('[aria-label="Copy transcript"]')).not.toBeNull();
    expect(sessionStorage.getItem(`vexa.meetingTranscriptOpen.${ROW}`)).toBe("1");
    act(() => { root.unmount(); });
    await renderView();
    expect(q("[data-transcript-toggle]")?.getAttribute("aria-expanded")).toBe("true");
  });

  it("the toggle is a real button and names the window it controls", async () => {
    await renderView();
    const t = q("[data-transcript-toggle]") as HTMLButtonElement;
    expect(t.tagName).toBe("BUTTON");
    expect(document.getElementById(t.getAttribute("aria-controls")!)).toBe(win());
  });
});

describe("the transcript is a different object from the notes", () => {
  it("its own labelled region, outside the notes, with conversational rows — not the notes' prose", async () => {
    await renderView();
    const panel = q("[data-meeting-transcript]")!;
    const notes = q("[data-meeting-notes]")!;
    expect(panel.getAttribute("role")).toBe("region");
    expect(document.getElementById(panel.getAttribute("aria-labelledby")!)?.textContent).toBe("Transcript");
    expect(document.getElementById(notes.getAttribute("aria-labelledby")!)?.textContent).toBe("Notes");
    expect(notes.contains(panel)).toBe(false);
    expect(panel.contains(notes)).toBe(false);
    // a contained tray: its own surface, border and radius
    expect(panel.style.borderRadius).toBe("12px");
    expect(panel.style.border).toContain("1px solid");
    expect(panel.style.background).not.toBe("");
    // rows: who and when in a left column, what was said beside it
    const rows = panel.querySelectorAll("[data-transcript-row]");
    expect(rows.length).toBe(5);
    expect(rows[0].querySelector("[data-transcript-who]")?.textContent).toMatch(/Jane|Ravi/);
    expect(notes.querySelector("[data-transcript-row]")).toBeNull();
  });

  it("empty template sections are not rendered as bare headings", async () => {
    fileBody = NOTES_MD + "\n## Report\n\n<!-- meeting:report:start -->\n\n<!-- meeting:report:end -->\n";
    await renderView();
    expect(q("[data-notes-body]")?.textContent).not.toContain("Report");
    expect(q("[data-notes-body]")?.textContent).toContain("Decisions");
  });
});

describe("no Brief, no Minutes", () => {
  it("the view never says either word — with notes or without", async () => {
    await renderView();
    expect(container.textContent).not.toMatch(/\bBrief\b|\bMinutes\b/);
    act(() => { root.unmount(); });
    notePath = null;
    await renderView();
    expect(container.textContent).not.toMatch(/\bBrief\b|\bMinutes\b/);
  });
});

describe("the view's rules, as functions", () => {
  it("notesForView drops every slot and keeps every other word", () => {
    expect(notesForView(NOTES_MD)).not.toContain("vexa:transcript");
    expect(notesForView(NOTES_MD)).toContain("- Ship the one view on Friday");
    expect(notesForView("no slot here")).toBe("no slot here");
    // a fenced example of the marker is text, not a slot
    expect(notesForView("```\n<!-- vexa:transcript meeting=1 -->\n```")).toContain("vexa:transcript");
  });

  it("notesFreshness says what the notes have taken in, and never guesses", () => {
    const segs = [0, 30, 65].map((t) => ({ text: "x", ts: t }));
    expect(notesFreshness({ cursor: "3", segments: segs })).toBe("from transcript up to 1:05");
    expect(notesFreshness({ cursor: "", segments: segs })).toBe("not yet updated from the transcript");
    expect(notesFreshness({ cursor: "abc", segments: segs })).toBe("not yet updated from the transcript");
  });

  it("withoutEmptySections drops a heading that heads only markers, and keeps one with content below", () => {
    const md = "# T\n\n## About\n<!-- a:start -->\n<!-- a:end -->\n## Decisions\n- x\n## People\n### Jane\nowns it\n```\n## not a heading\n```\n";
    const out = withoutEmptySections(md);
    expect(out).not.toContain("## About");
    expect(out).toContain("<!-- a:start -->");
    expect(out).toContain("## Decisions");
    expect(out).toContain("## People");
    expect(out).toContain("## not a heading");
  });

  it("transcriptTail is the last three said lines, counting the one still being said", () => {
    const segs = ["a", "b", " ", "c", "d"].map((text, i) => ({ text, completed: i < 4 }));
    expect(transcriptTail(segs).map((s) => s.text)).toEqual(["b", "c", "d"]);
  });
});
