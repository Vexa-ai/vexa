/** ONE MEETING PAGE (#1783, founder 2026-10-14 after a real call: "the meeting-with-transcript page
 *  is created separately from the meeting page; the transcript page has no playback / delete and no
 *  proper, nice meeting UI"). Same harness as meetingView.test.tsx.
 *
 *  Origin of the harness: THE ONE MEETING VIEW (founder-approved design, 2026-10-11) — driven through the view itself,
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

import { MeetingView } from "../MeetingView";
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
let noteWorkspace = "";
let fold = false;
let folds = 0;
let recordings: unknown[] = [];
let fileBody: string | null = NOTES_MD;
/** names the link resolver answers with a page (title → path) */
const linkHits: Record<string, string> = {};
const calls: { method: string; url: string; body?: string }[] = [];
const asks: { prompt?: string; display?: string; intent?: ChatIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  for (const k of Object.keys(linkHits)) delete linkHits[k];
  try { localStorage.setItem("vexa.meetingTranscriptHeight", "0"); } catch { /* the person collapsed it: these tests open it themselves */ }
  container = document.createElement("div");
  document.body.appendChild(container);
  notePath = NOTE; fileBody = NOTES_MD; calls.length = 0; asks.length = 0;
  noteWorkspace = ""; fold = false; folds = 0; recordings = [];
  liveState = {}; durableState.lines = [];
  try { sessionStorage.clear(); } catch { /* */ }
  clearPending(); resetActs();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.startsWith("/api/recordings?")) return json({ recordings: recordings });
    if (url.startsWith("/api/links/resolve")) {
      const refs: string[] = JSON.parse(String(init?.body ?? "{}")).refs ?? [];
      return json({ results: refs.map((r) => linkHits[r]
        ? { ref: r, title: r, url: null, access: "readable", path: linkHits[r], slug: "personal" }
        : { ref: r, title: r, url: null, access: "readable", missing: true }) });
    }
    if (url.startsWith("/api/meeting/note") && method === "POST" && noteWorkspace) {
      folds += 1; fold = false;
      return json({ path: notePath, workspace: noteWorkspace, created: false, folded: true });
    }
    if (url.startsWith("/api/meeting/note") && method === "POST") {
      notePath = NOTE; fileBody = NOTES_MD.replace("transcript_cursor: 4", "transcript_cursor:");
      return json({ path: NOTE, created: true });
    }
    if (url.startsWith("/api/meeting/note")) return json({ path: notePath, workspace: noteWorkspace, title: "", transcript: notePath ? ROW : "", cursor: notePath ? "4" : "" });
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


const ENDED = { status: "past", live_status: "completed", end_time: new Date(AT + 37 * 60000).toISOString() };
const GENERIC_MD = NOTES_MD.replace("# Design review", "# Google Meet meeting").replace("title: Design review", "title: Google Meet meeting");
const buttons = (label: string) => [...container.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label);

describe("one page, its real name, no paths", () => {
  it("the title is the meeting's name, never the minted placeholder — and the placeholder is not a second title", async () => {
    fileBody = GENERIC_MD;
    await renderView({ title: "Side event — planning call" });
    expect(q("[data-doc-name]")?.textContent).toBe("Side event — planning call");
    expect(q("[data-notes-body]")?.textContent).not.toContain("Google Meet meeting");
  });

  it("no raw path anywhere on the view — not as text, not as a tooltip", async () => {
    await renderView();
    expect(container.textContent).not.toContain("kg/entities");
    expect([...container.querySelectorAll("[title]")].map((e) => e.getAttribute("title"))).not.toContain(NOTE);
    expect(q("[data-notes-path]")).toBeNull();
  });

  it("a brief in the meeting's workspace is read from that workspace, and nothing is folded into it", async () => {
    noteWorkspace = "ait-lisbon"; fold = true; notePath = "meetings/2026-10-14-planning-call.md";
    await renderView();
    expect(folds).toBe(0);
    const reads = calls.filter((c) => c.url.startsWith("/api/workspace/file"));
    expect(reads.length).toBeGreaterThan(0);
    expect(reads.every((c) => c.url.includes("slug=ait-lisbon"))).toBe(true);
    await act(async () => { window.dispatchEvent(new Event(WORKSPACE_COMMIT_EVENT)); });
    await flush();
    expect(folds).toBe(0);
  });

  it("Update notes on a brief names its workspace", async () => {
    noteWorkspace = "ait-lisbon"; notePath = "meetings/2026-10-14-planning-call.md";
    await renderView(ENDED);
    await act(async () => { fireEvent.click(q('[data-notes-act="update"]')!); });
    await flush();
    expect(asks[0].intent).toMatchObject({ kind: "extend", path: "meetings/2026-10-14-planning-call.md", workspace: "ait-lisbon", meeting: ROW });
  });
});

describe("the header's people", () => {
  it("lists only identified people — a speaker label is nobody (founder, 2026-10-15)", async () => {
    await renderView({ ...ENDED, participants: [{ name: "Speaker", role: "", initials: "S" }, { name: "Speaker 2", role: "", initials: "S" },
      { name: "Unknown", role: "", initials: "U" }, { name: "Dmitry Grankin", role: "", initials: "DG" }] });
    const people = q("[data-meeting-people]")!;
    expect(people.textContent).toContain("Dmitry Grankin");
    expect(people.textContent).not.toMatch(/Speaker|Unknown/);
  });

  it("a person with a page is that page's entity chip; anyone else is a name", async () => {
    // (a name no earlier test asked about: the resolver caches its answers)
    linkHits["Valeriia Petrova"] = "kg/entities/person/valeriia-petrova.md";
    await renderView({ ...ENDED, participants: [{ name: "Valeriia Petrova", role: "", initials: "VP" }, { name: "Ravi", role: "", initials: "R" }] });
    for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    const chip = q("[data-meeting-person-chip] .vx-chip.vx-entity");
    expect(chip?.textContent).toContain("Valeriia Petrova");
    expect(q("[data-meeting-people]")!.textContent).toContain("Ravi");
    expect(container.querySelectorAll("[data-meeting-person-chip]")).toHaveLength(1);
  });

  it("nobody identified: nothing there", async () => {
    await renderView({ ...ENDED, participants: [{ name: "Speaker 1", role: "", initials: "S" }] }, []);
    expect(q("[data-meeting-people]")).toBeNull();
  });
});

describe("the header", () => {
  it("says when, how long, who, and that it ended", async () => {
    await renderView({ ...ENDED, participants: [{ name: "Jane Doe", role: "", initials: "JD" }, { name: "Ravi", role: "", initials: "R" }] });
    expect(q("[data-meeting-status]")?.textContent).toBe("Ended");
    // the platform is an icon, named for a screen reader — never a word in the line (UI decisions 14)
    expect(q("[data-meeting-platform]")?.getAttribute("aria-label")).toBe("Google Meet");
    expect(q("[data-meeting-metadata]")?.textContent).not.toContain("Google Meet");
    expect(q("[data-meeting-metadata]")?.textContent).toMatch(/.+ · (\d+ h )?\d+ min/);
    const people = q("[data-meeting-people]")!;
    expect(people.textContent).toContain("Jane Doe");
    expect(people.textContent).toContain("JD");
  });

  it("while it runs it says Live", async () => {
    await renderView();
    expect(q("[data-meeting-status]")?.textContent).toBe("Live");
  });

  it("Update notes is on the view exactly once; Send bot again and Export are not loose buttons", async () => {
    await renderView(ENDED);
    expect(buttons("Update notes")).toHaveLength(1);
    expect(buttons("Send bot again")).toHaveLength(0);
    expect(buttons("Export")).toHaveLength(0);
    expect(q('[aria-label="More meeting actions"]')).not.toBeNull();
    expect(q('[data-meeting-act="delete"]')).toBeNull();        // Delete lives inside "…"
    expect(container.textContent).toContain("Share");
  });

  it("Delete asks for the word first, says saved pages stay, then calls the existing delete", async () => {
    await renderView(ENDED);
    await act(async () => { fireEvent.click(q('[aria-label="More meeting actions"]')!); });
    const del = [...document.querySelectorAll('[role="menuitem"]')].find((b) => b.textContent === "Delete…") as HTMLElement;
    await act(async () => { fireEvent.click(del); });
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog.textContent).toContain("Pages saved in a workspace stay");
    const confirm = [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Delete meeting") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await act(async () => { fireEvent.change(dialog.querySelector("input")!, { target: { value: "delete" } }); });
    expect(confirm.disabled).toBe(false);
  });

  it("while the meeting runs, \"…\" holds Stop bot and no Delete; neither is a loose button", async () => {
    await renderView();
    expect(q('[data-meeting-act="delete"]')).toBeNull();
    expect(q('[data-meeting-act="stop-bot"]')).toBeNull();
    await act(async () => { fireEvent.click(q('[aria-label="More meeting actions"]')!); });
    const items = [...document.querySelectorAll('[role="menuitem"]')].map((b) => b.textContent);
    expect(items).toContain("Stop bot");
    expect(items).not.toContain("Delete…");
  });
});

describe("the language is in \"…\", not a row of the tools", () => {
  it("live: no Language row and no Change language button; \"…\" offers it, and it opens the picker", async () => {
    await renderView();
    expect(container.textContent).not.toContain("Not reported yet");
    expect(buttons("Change language")).toHaveLength(0);
    await act(async () => { fireEvent.click(q('[aria-label="More meeting actions"]')!); });
    const item = [...document.querySelectorAll('[role="menuitem"]')].find((b) => b.textContent === "Change language…") as HTMLElement;
    expect(item).toBeTruthy();
    await act(async () => { fireEvent.click(item); });
    expect(buttons("Switch language")).toHaveLength(1);
  });

  it("once the language is known it is a small label", async () => {
    await renderView({ transcription_language: { language: "ru" } });
    expect(q("[data-meeting-language]")?.textContent).toBeTruthy();
    expect(container.textContent).not.toContain("Language");
  });
});

describe("the recording, inline", () => {
  it("a finished meeting with audio shows ONE playback control — the inline player — which plays it", async () => {
    recordings = [{ id: 7, media_files: [{ id: 9, type: "audio", format: "webm", capture_started_at_ms: AT, duration_seconds: 2220 }] }];
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    await renderView(ENDED, live());
    expect(q('audio[src="/api/recordings/7/media/9/raw?type=audio"]')).not.toBeNull();
    // founder, 2026-10-14: the big "Play recording" button in the acts row duplicated the player
    expect(q('[data-meeting-act="play"]')).toBeNull();
    const plays = [...container.querySelectorAll("button")].filter((b) => /^(Play recording|Pause)$/.test(b.getAttribute("aria-label") ?? b.textContent?.trim() ?? ""));
    expect(plays).toHaveLength(1);
    expect(q("[data-meeting-actions]")!.contains(plays[0])).toBe(false);
    expect(q("[data-meeting-actions] .vx-mv-btn-primary")).toBeNull();
    await act(async () => { fireEvent.click(plays[0]); });
    expect(play).toHaveBeenCalled();
    play.mockRestore();
  });

  it("no recording: no Play button", async () => {
    await renderView(ENDED, live());
    expect(q('[data-meeting-act="play"]')).toBeNull();
  });
});

describe("the transcript's search", () => {
  it("shows only the speaker turns that say it, the words marked", async () => {
    await renderView();
    await act(async () => { fireEvent.click(q("[data-transcript-toggle]")!); });
    await act(async () => { fireEvent.click(q("[data-transcript-search-toggle]")!); });
    const box = q("[data-transcript-search]") as HTMLInputElement;
    expect(box).not.toBeNull();
    await act(async () => { fireEvent.change(box, { target: { value: "header" } }); });
    expect(transcriptText()).toContain("then the header");
    expect(transcriptText()).not.toContain("first we agree the scope");
    expect(q("[data-transcript-match]")?.textContent).toBe("header");
    await act(async () => { fireEvent.change(box, { target: { value: "zzz" } }); });
    expect(q("[data-transcript-no-match]")).not.toBeNull();
  });
});
