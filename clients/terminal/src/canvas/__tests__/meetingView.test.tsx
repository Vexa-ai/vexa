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
const { vnState, vnStop } = vi.hoisted(() => ({
  vnState: { state: { phase: "idle", sentSec: 0, receivedSec: 0, level: 0, paused: false } as Record<string, unknown> },
  vnStop: vi.fn(async () => {}),
}));
vi.mock("../../minutes/voiceNote", async (orig) => ({
  ...(await orig<typeof import("../../minutes/voiceNote")>()),
  useVoiceNote: () => vnState.state, stopVoiceNote: vnStop,
}));
vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => ({ ...EMPTY_LIVE, ...liveState }),
}));

import { EDIT_NOTE_EVENT, MeetingView, NOTES_POLL_MS, sameNote } from "../MeetingView";
import { ASK_CHAT_EVENT, ServicesProvider, WORKSPACE_COMMIT_EVENT, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
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
  // the person's own last choice: collapsed — the default (open at 30%) has its own test below
  try { sessionStorage.clear(); localStorage.setItem("vexa.meetingTranscriptHeight", "0"); } catch { /* */ }
  clearPending(); resetActs();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.startsWith("/api/meeting/note") && method === "POST") {
      notePath = NOTE; fileBody = NOTES_MD.replace("transcript_cursor: 4", "transcript_cursor:");
      return json({ path: NOTE, created: true });
    }
    // the cursor is read off the FILE, as the server does (`meeting_note.describe`)
    if (url.startsWith("/api/meeting/note")) return json({ path: notePath, transcript: notePath ? ROW : "", cursor: notePath ? (/^transcript_cursor:[ \t]*(\d*)/m.exec(fileBody ?? "")?.[1] ?? "") : "" });
    if (url.startsWith("/api/workspace/file") && method === "PUT") return json({ path: NOTE, written: true });
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
  it("shows the header, the notes (labelled Notes, no file path — #1783) and the transcript under them", async () => {
    await renderView();
    expect(q("[data-doc-name]")?.textContent).toBe("Design review");
    // the platform is an icon, named for a screen reader — never a word in the line (UI decisions 14)
    expect(q("[data-meeting-platform]")?.getAttribute("aria-label")).toBe("Google Meet");
    expect(q("[data-meeting-metadata]")?.textContent).not.toContain("Google Meet");
    expect(q("[data-meeting-metadata]")?.textContent).toMatch(/.+ · (\d+ h )?\d+ min/);
    expect(q("[data-meeting-people]")?.textContent).toContain("Ravi");
    expect(q("[data-meeting-status]")?.textContent).toBe("Live");
    expect(q("#notes-147")?.textContent).toBe("Notes");
    expect(q("[data-notes-path]")).toBeNull();
    expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
    expect(q("[data-notes-freshness]")?.textContent).toMatch(/^Updated .+ · transcript to 0:45$/);
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
    expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
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
const geom = { scrollHeight: 1000, clientHeight: 128, activeTop: -600, viewW: 0 };
const tops = new WeakMap<Element, number>();
const isWin = (el: Element) => el.hasAttribute("data-transcript-window");
const saved: Record<string, PropertyDescriptor | undefined> = {};
function installLayout() {
  for (const k of ["scrollHeight", "clientHeight", "scrollTop", "clientWidth"]) saved[k] = Object.getOwnPropertyDescriptor(Element.prototype, k);
  saved.rect = Object.getOwnPropertyDescriptor(Element.prototype, "getBoundingClientRect");
  Object.defineProperty(Element.prototype, "scrollHeight", { configurable: true, get() { return isWin(this) ? geom.scrollHeight : 0; } });
  Object.defineProperty(Element.prototype, "clientHeight", { configurable: true, get() { return isWin(this) ? geom.clientHeight : 0; } });
  Object.defineProperty(Element.prototype, "clientWidth", { configurable: true, get() { return this.hasAttribute("data-meeting-view") ? geom.viewW : 0; } });
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

describe("the transcript — a drawer at the bottom that opens upward over the note", () => {
  beforeEach(installLayout);
  afterEach(removeLayout);
  const openDrawer = async () => { await act(async () => { fireEvent.click(q("[data-transcript-toggle]")!); }); };

  it("collapsed it is the latest line, full width — a live dot and Speaker: text, nothing else", async () => {
    await renderView();
    const toggle = q("[data-transcript-toggle]")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-label")).toBe("Show all");
    // founder, 2026-10-15: "the left part is not [useful]" — no icon, no word, no count collapsed
    expect(q("[data-transcript-count]")).toBeNull();
    expect(q(".vx-mv-tray-icon")).toBeNull();
    expect(q("[data-transcript-live] .vx-mv-live-dot")).not.toBeNull();
    const row = q(".vx-mv-tray-row")!;
    const visible = [...row.childNodes].filter((n) => !(n as HTMLElement).classList?.contains("vx-mv-sr"))
      .map((n) => (n as HTMLElement).querySelector?.(".vx-mv-sr") ? "" : n.textContent).join("");
    expect(visible).toBe("Jane: collapsed by default");
    expect(q("[data-transcript-preview]")?.textContent).toBe("Jane: collapsed by default");
    expect(win().hidden).toBe(true);
    expect(q("[data-transcript-search]")).toBeNull();
    // the drawer is the view's foot: after the note, outside its scroller
    expect(q("[data-meeting-main]")!.contains(q("[data-meeting-transcript]"))).toBe(false);
    expect(q("[data-meeting-body]")!.lastElementChild).toBe(q("[data-transcript-drawer]"));
  });

  it("Show all opens it upward — the note shrinks above it — with search, copy, on the newest line; Collapse folds it", async () => {
    await renderView();
    await openDrawer();
    const drawer = q("[data-transcript-drawer]")!;
    expect(drawer.hasAttribute("data-open")).toBe(true);
    expect(drawer.style.height).toBe("320px");                     // the default open height
    expect(drawer.style.flex).toBe("0 0 auto");
    expect(q("[data-meeting-main]")!.contains(q("[data-meeting-notes]"))).toBe(true);
    expect(container.querySelectorAll("[data-meeting-transcript]")).toHaveLength(1);
    expect(win().hidden).toBe(false);
    expect(q("[data-transcript-search-toggle]")).not.toBeNull();   // search is an icon that opens a field
    await act(async () => { fireEvent.click(q("[data-transcript-search-toggle]")!); });
    expect(q("[data-transcript-search]")).not.toBeNull();
    expect(q('[aria-label="Copy transcript"]')).not.toBeNull();
    // one header row — no second row of buttons
    expect(container.querySelectorAll("[data-meeting-transcript] .vx-mv-tray-row")).toHaveLength(1);
    expect(win().scrollTop).toBe(872);
    expect(q("[data-transcript-toggle]")?.getAttribute("aria-label")).toBe("Collapse");
    await openDrawer();
    expect(q("[data-transcript-drawer]")!.hasAttribute("data-open")).toBe(false);
  });

  it("with no choice of the person's yet, it opens at 30% of the sidebar (founder: \"up 30% by default\")", async () => {
    localStorage.removeItem("vexa.meetingTranscriptHeight");
    const saved = Object.getOwnPropertyDescriptor(Element.prototype, "clientHeight");
    Object.defineProperty(Element.prototype, "clientHeight", { configurable: true,
      get() { return this.hasAttribute("data-meeting-body") ? 800 : this.hasAttribute("data-transcript-window") ? geom.clientHeight : 0; } });
    try {
      await renderView();
      const drawer = q("[data-transcript-drawer]")!;
      expect(drawer.hasAttribute("data-open")).toBe(true);
      expect(drawer.style.height).toBe("240px");
      await act(async () => { fireEvent.click(q("[data-transcript-toggle]")!); });
      expect(q("[data-transcript-drawer]")!.hasAttribute("data-open")).toBe(false);   // collapsed only when the person collapses it
      expect(localStorage.getItem("vexa.meetingTranscriptHeight")).toBe("0");
    } finally { if (saved) Object.defineProperty(Element.prototype, "clientHeight", saved); }
  });

  it("is resizable from its top edge — by keyboard or drag — remembered for the person; a double-click toggles", async () => {
    await renderView();
    const handle = q("[data-transcript-resize]")!;
    expect(handle.getAttribute("role")).toBe("separator");
    expect(handle.getAttribute("aria-orientation")).toBe("horizontal");
    await act(async () => { fireEvent.keyDown(handle, { key: "ArrowUp" }); });   // from collapsed: opens a step
    const h1 = parseInt(q("[data-transcript-drawer]")!.style.height, 10);
    expect(h1).toBeGreaterThan(0);
    await act(async () => { fireEvent.keyDown(handle, { key: "ArrowUp" }); });
    expect(parseInt(q("[data-transcript-drawer]")!.style.height, 10)).toBe(h1 + 32);
    expect(localStorage.getItem("vexa.meetingTranscriptHeight")).toBe(String(h1 + 32));
    await act(async () => { fireEvent.doubleClick(handle); });
    expect(q("[data-transcript-drawer]")!.hasAttribute("data-open")).toBe(false);
    await act(async () => { fireEvent.doubleClick(handle); });
    expect(q("[data-transcript-drawer]")!.style.height).toBe(`${h1 + 32}px`);   // back to the last height
    act(() => { root.unmount(); });
    await renderView();
    expect(q("[data-transcript-drawer]")!.style.height).toBe(`${h1 + 32}px`);   // remembered
    try { localStorage.removeItem("vexa.meetingTranscriptHeight"); } catch { /* */ }
  });

  it("live: new lines keep it on the bottom; a person who scrolls up is left there with Jump to latest", async () => {
    await renderView({}, live(3));
    await openDrawer();
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

  it("a FINISHED meeting starts collapsed too, its last line in the preview", async () => {
    durableState.lines = LINES.map((text, i) => ({ speaker: "Jane", text, offsetSeconds: i * 15 }));
    await renderView({ status: "past", live_status: "completed", session_uid: undefined }, []);
    expect(q("[data-transcript-toggle]")?.getAttribute("aria-expanded")).toBe("false");
    expect(q("[data-transcript-live]")).toBeNull();
    expect(q("[data-transcript-preview]")?.textContent).toContain("collapsed by default");
  });

  it("playback: the played line is highlighted and kept in view; a manual scroll stops following until Follow playback", async () => {
    await renderView({}, live());
    await openDrawer();
    const { registerPlayback } = await import("../../minutes/meetingPlayback");
    const seek = vi.fn();
    const player = registerPlayback(ROW, { seek, pause: vi.fn(), originMs: AT });
    try {
      await act(async () => { player.update(31, true); });            // inside the third line (30s)
      const active = q("[data-playback-active]")!;
      expect(active.textContent).toContain("notes go on top");
      expect(win().scrollTop).toBe(872 - 600 - (128 - 20) / 2);
      await userScroll(500);
      expect(q("[data-transcript-follow-playback]")?.textContent).toBe("Follow playback");
      await act(async () => { player.update(32); });
      expect(win().scrollTop).toBe(500);                             // the person's place is kept
      await act(async () => { fireEvent.click(q("[data-transcript-follow-playback]")!); });
      expect(win().scrollTop).not.toBe(500);
      // clicking a line plays from it
      await act(async () => { fireEvent.click(q("[data-playback-active]")!); });
      expect(seek).toHaveBeenCalled();
    } finally { await act(async () => { player.dispose(); }); }
  });

  it("the toggle is a real button and names the window it controls", async () => {
    await renderView();
    const t = q("[data-transcript-toggle]") as HTMLButtonElement;
    expect(t.tagName).toBe("BUTTON");
    expect(document.getElementById(t.getAttribute("aria-controls")!)).toBe(win());
  });
});

describe("the meeting tools stay on top; the note scrolls between them and the drawer", () => {
  it("three zones: the header outside the scroller, the note inside it, the drawer at the foot", async () => {
    await renderView();
    const view = q("[data-meeting-view]")!;
    const top = q("[data-meeting-top]")!;
    expect(view.firstElementChild).toBe(top);
    expect(top.contains(q("[data-meeting-header]"))).toBe(true);
    expect(q("[data-meeting-main]")!.contains(q("[data-meeting-header]"))).toBe(false);
    expect(q("[data-meeting-main]")!.style.overflowY).toBe("auto");
  });

  it("a click on the note opens nothing — it is read like a page (founder 2026-10-15: no one-click edit)", async () => {
    await renderView();
    expect(container.textContent).not.toContain(NOTE);
    expect(q("[data-notes-open]")).toBeNull();
    expect([...container.querySelectorAll("button")].some((b) => /^(Edit|Open as page)$/.test(b.textContent?.trim() ?? ""))).toBe(false);
    await act(async () => { fireEvent.click(q("[data-notes-body]")!); });
    expect(q("[data-notes-editor]")).toBeNull();
  });

  it("'…' → Edit notes edits it like any page: the words only, the markers put back on save, to the file it read", async () => {
    fileBody = NOTES_MD.replace("- Ship the one view on Friday",
      "<!-- meeting:decisions:start -->\n- Ship the one view on Friday\n<!-- meeting:decisions:end -->");
    await renderView();
    await act(async () => { window.dispatchEvent(new CustomEvent(EDIT_NOTE_EVENT, { detail: { meetingId: ROW } })); });
    const editor = q("[data-notes-editor]")!;
    expect(editor).not.toBeNull();
    expect(editor.textContent).toContain("Ship the one view on Friday");
    expect(editor.textContent).not.toContain("<!--");
    await act(async () => { fireEvent.click(q("[data-notes-save]")!); });
    await flush();
    const put = calls.find((c) => c.method === "PUT" && c.url.startsWith("/api/workspace/file"))!;
    const sent = JSON.parse(put.body!);
    // the desk is named, never defaulted (a default followed the chat's workspace, #1783)
    expect(sent).toMatchObject({ path: NOTE, slug: "personal" });
    expect(sent.content).toContain(`<!-- vexa:transcript meeting=${ROW} -->`);
    expect(sent.content).toContain("## Decisions\n<!-- meeting:decisions:start -->\n- Ship the one view on Friday\n<!-- meeting:decisions:end -->");
    expect(q("[data-notes-editor]")).toBeNull();
  });

  it("the header's '…' offers Edit notes", async () => {
    await renderView();
    expect(container.querySelector('[aria-label="More meeting actions"]')).not.toBeNull();
  });
});

describe("the note renders as a page (UI decisions 9, 12, 13)", () => {
  it("no marker text, no outline, and a known person is an entity chip", async () => {
    fileBody = ["---", "type: meeting", `meeting: ${ROW}`, "transcript_cursor: 4", "---", "",
      `<!-- vexa:transcript meeting=${ROW} -->`, "", "## About", "<!-- meeting:about:start -->",
      "Planning with [[Jane Doe]].", "<!-- meeting:about:end -->", "", "## Decisions", "- a", "## People", "- b", "## Open questions", "- c", "## Commitments", "- d", ""].join("\n");
    const base = (globalThis.fetch as unknown as (i: RequestInfo | URL, n?: RequestInit) => Promise<Response>);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/links/resolve")) {
        return json({ results: [{ ref: "Jane Doe", title: "Jane Doe", url: null, access: "readable", path: "kg/entities/person/jane-doe.md", slug: "personal" }] });
      }
      return base(input, init);
    }));
    await renderView();
    for (let i = 0; i < 4; i++) await flush();
    const body = q("[data-notes-body]")!;
    expect(body.textContent).not.toContain("<!--");
    expect(body.textContent).not.toContain("meeting:about");
    expect(body.querySelector('nav[aria-label="On this page"]')).toBeNull();
    const chip = body.querySelector('.vx-chip.vx-entity[data-kind="person"]');
    expect(chip?.textContent).toContain("Jane Doe");
  });
});

describe("a report in a Personal workspace that was switched off (#1783, founder 2026-10-15)", () => {
  it("is shown from the text the server hands back, read only — no Update, no Edit, no file read", async () => {
    const base = (globalThis.fetch as unknown as (i: RequestInfo | URL, n?: RequestInit) => Promise<Response>);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ method: (init?.method ?? "GET").toUpperCase(), url });
      if (url.startsWith("/api/meeting/note") && (init?.method ?? "GET") === "GET") {
        return json({ path: NOTE, workspace: "", transcript: ROW, cursor: "4", readonly: true, content: NOTES_MD });
      }
      return base(input, init);
    }));
    await renderView();
    expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
    expect(q("[data-notes-freshness]")?.textContent).toContain("read only");
    expect(q('[data-notes-act="update"]')).toBeNull();
    expect(calls.some((c) => c.url.startsWith("/api/workspace/file"))).toBe(false);
  });
});

describe("the collapsed drawer with nothing said yet", () => {
  it("says Listening… while live, and Transcript only when nothing is listening", async () => {
    await renderView({}, []);
    expect(q("[data-transcript-preview]")?.textContent).toBe("Listening…");
    act(() => { root.unmount(); });
    await renderView({ status: "completed", live_status: "completed", end_time: new Date(AT + 3600000).toISOString() }, []);
    expect(q("[data-transcript-preview]")?.textContent).toBe("Transcript");
  });
  it("opened, the drawer's header has the word, the count and the live label", async () => {
    await renderView();
    await act(async () => { fireEvent.click(q("[data-transcript-toggle]")!); });
    expect(q("[data-transcript-count]")?.textContent).toBe("5");
    expect(q("[data-transcript-live]")?.textContent).toBe("live");
  });
});

describe("a write the view was not told about shows up live (#1783: 'it never writes to notes here')", () => {
  it("the agent's write, a background Update, the flow: the note re-reads on its own within seconds", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      fileBody = ["---", "type: meeting", `meeting: ${ROW}`, "transcript_cursor:", "---", "", `<!-- vexa:transcript meeting=${ROW} -->`, ""].join("\n");
      await renderView();
      expect(q("[data-notes-body]")).toBeNull();
      expect(q("[data-notes-empty]")?.textContent).toContain("Nothing written in the notes yet");
      // written somewhere else — no commit event reaches this tab
      fileBody = NOTES_MD;
      await act(async () => { vi.advanceTimersByTime(NOTES_POLL_MS); });
      await flush();
      expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
      expect(q("[data-notes-freshness]")?.textContent).toContain("transcript to");
    } finally { vi.useRealTimers(); }
  });

  it("a commit in this tab re-reads at once", async () => {
    fileBody = ["---", "type: meeting", `meeting: ${ROW}`, "transcript_cursor:", "---", ""].join("\n");
    await renderView();
    fileBody = NOTES_MD;
    await act(async () => { window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT)); });
    await flush();
    expect(q("[data-notes-body]")?.textContent).toContain("Ship the one view on Friday");
  });

  it("a poll that finds the same note changes nothing", () => {
    const a = { phase: "ready" as const, path: NOTE, workspace: "", body: "x", cursor: "4", updatedAt: 1 };
    expect(sameNote(a, { ...a })).toBe(true);
    expect(sameNote(a, { ...a, body: "y" })).toBe(false);
    expect(sameNote(a, { ...a, cursor: "5" })).toBe(false);
    expect(sameNote({ phase: "none" }, { phase: "none" })).toBe(true);
    expect(sameNote({ phase: "none" }, a)).toBe(false);
  });

  it("the empty note is truthful about an update that found nothing", async () => {
    fileBody = ["---", "type: meeting", `meeting: ${ROW}`, "transcript_cursor: 3", "---", "", `<!-- vexa:transcript meeting=${ROW} -->`, ""].join("\n");
    await renderView();
    const t = q("[data-notes-empty]")?.textContent ?? "";
    expect(t).toContain("the last update read the transcript");
    expect(t).not.toContain("Nothing written in the notes yet");
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
    // (the tray's look — surface, 1px border, 12px radius — is the design-system class in meetingView.css)
    expect(panel.classList.contains("vx-mv-tray")).toBe(true);
    expect(panel.dataset.mode).toBe("collapsed");
    // rows: who and when in a left column, what was said beside it
    const rows = panel.querySelectorAll("[data-transcript-row]");
    expect(rows.length).toBe(5);
    expect(rows[0].querySelector("[data-transcript-who]")?.textContent).toMatch(/Jane|Ravi/);
    expect(notes.querySelector("[data-transcript-row]")).toBeNull();
  });

  it("a note that is still the empty template says nothing is written yet — not a blank box", async () => {
    fileBody = ["---", "type: meeting", `meeting: ${ROW}`, "---", "", "# Google Meet meeting", "", `<!-- vexa:transcript meeting=${ROW} -->`, "",
      "## Decisions", "<!-- meeting:decisions:start -->", "", "<!-- meeting:decisions:end -->", ""].join("\n");
    await renderView();
    expect(q("[data-notes-body]")).toBeNull();
    expect(q("[data-notes-empty]")?.textContent).toContain("Nothing written in the notes yet");
    expect(q('[data-notes-act="update"]')).not.toBeNull();
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
  it("notesForView leaves the frontmatter out — the notes start after it", () => {
    expect(notesForView(NOTES_MD)).not.toContain("transcript_cursor");
    expect(notesForView(NOTES_MD).trimStart().startsWith("# Design review")).toBe(true);
  });

  it("notesForView drops every slot and keeps every other word", () => {
    expect(notesForView(NOTES_MD)).not.toContain("vexa:transcript");
    expect(notesForView(NOTES_MD)).toContain("- Ship the one view on Friday");
    expect(notesForView("no slot here")).toBe("no slot here");
    // a fenced example of the marker is text, not a slot
    expect(notesForView("```\n<!-- vexa:transcript meeting=1 -->\n```")).toContain("vexa:transcript");
  });

  it("notesFreshness says what the notes have taken in, and never guesses", () => {
    const segs = [0, 30, 65].map((t) => ({ text: "x", ts: t }));
    expect(notesFreshness({ cursor: "3", segments: segs })).toBe("transcript to 1:05");
    // founder, 2026-10-15: "from transcript up to 497792:23:39" — an epoch `ts` read as a duration
    const wall = [0, 30, 842].map((t) => ({ text: "x", ts: 1792011422 + t }));
    expect(notesFreshness({ cursor: "3", segments: wall })).toBe("transcript to 14:02");
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

/** A VOICE NOTE in the pages pane while it records (founder, 2026-10-13: "does not look good the
 *  right sidebar"): the view takes the pane's width, the count counts what is on screen, the running
 *  transcript is not a four-line window, the recording and Stop are in the page itself, and the notes
 *  say they come when it stops instead of asking for them mid-recording. */
describe("a voice note while it records", () => {
  const NOTE_ROW = { platform: "voice_note", native_id: "vn-abc", title: "Voice note", participants: [] };
  const recording = () => { vnState.state = { phase: "recording", nativeId: "vn-abc", sentSec: 42, receivedSec: 41, maxSec: 1800, level: 0.2, paused: false }; };
  afterEach(() => { vnState.state = { phase: "idle", sentSec: 0, receivedSec: 0, level: 0, paused: false }; vnStop.mockClear(); });

  it("the view fills the pane it is in (a flex child that grows), its content centred under a max width", async () => {
    await renderView(NOTE_ROW);
    const root = q("[data-meeting-view]")!;
    expect(root.style.flex).toBe("1 1 0%");
    expect(root.style.width).toBe("100%");
    expect(q("[data-meeting-main]")?.getAttribute("data-centred")).toBe("");
  });

  it("the count includes the line still being said", async () => {
    await renderView(NOTE_ROW, [{ id: "p1", speaker: "Ann", text: "still talking", t: 0, tsMs: AT, completed: false }]);
    expect(q("[data-transcript-count]")?.textContent).toBe("1");
  });

  it("the running transcript is the page's main content, not a four-line window", async () => {
    await renderView(NOTE_ROW);
    const win = q("[data-transcript-window]")!;
    expect(win.style.maxHeight).not.toBe("128px");
    expect(win.getAttribute("data-live-note")).toBe("");
  });

  it("the page header shows the recording clock and Stop", async () => {
    recording();
    await renderView(NOTE_ROW);
    const bar = q("[data-meeting-view] header [data-voice-note-clock]");
    expect(bar?.textContent).toMatch(/Recording 0:42/);
    const stop = [...container.querySelectorAll("[data-meeting-view] header button")].find((b) => b.textContent === "Stop") as HTMLButtonElement;
    expect(stop).toBeTruthy();
    await act(async () => { fireEvent.click(stop); });
    expect(vnStop).toHaveBeenCalled();
  });

  it("no notes yet: they are made when it stops — no Create button mid-recording", async () => {
    notePath = null;
    await renderView(NOTE_ROW);
    expect(q("[data-notes-empty]")?.textContent).toContain("Notes are written when you stop recording");
    expect(q('[data-notes-act="create"]')).toBeNull();
  });

  it("a finished voice note offers Create notes as any meeting does", async () => {
    notePath = null;
    await renderView({ ...NOTE_ROW, status: "completed", live_status: "completed" });
    expect(q('[data-notes-act="create"]')).not.toBeNull();
  });
});
