/** The meeting view with transcript signals: the brief on the page as "Listening for: …" (edited
 *  through the page), chips on the transcript lines from the meeting's annotations — and none of it
 *  when signals are switched off. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent } from "@testing-library/react";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let meetingsState: unknown[] = [];
let liveState: Record<string, unknown> = {};
const sig = vi.hoisted(() => ({ annotations: {} as Record<string, unknown>, asked: [] as unknown[] }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => meetingsState,
  useLiveMeetingsConnection: () => true,
  refreshMeetings: vi.fn(),
  fetchDurableTranscript: vi.fn(async () => ({ lines: [] })),
}));
vi.mock("../../minutes/voiceNote", async (orig) => ({
  ...(await orig<typeof import("../../minutes/voiceNote")>()),
  useVoiceNote: () => ({ phase: "idle", sentSec: 0, receivedSec: 0, level: 0, paused: false }), stopVoiceNote: vi.fn(),
}));
vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => ({ transcript: [], issues: [], connected: false, ended: false, reconnects: 0, ...liveState }),
}));
vi.mock("../../surfaces/meetingSignals", async (orig) => ({
  ...(await orig<typeof import("../../surfaces/meetingSignals")>()),
  useMeetingSignals: (...args: unknown[]) => { sig.asked.push(args); return args[2] ? sig.annotations : {}; },
}));

import { MeetingView } from "../MeetingView";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
import { setFeatureOverride } from "../../platform/features";

const ROW = "147";
const NOTE = "kg/entities/meeting/2026-10-11-1400-install-review.md";
const AT = Date.UTC(2026, 9, 11, 14, 0, 0);
const PAGE = [
  "---", "type: meeting", `meeting: ${ROW}`, "title: Install review", "listen:", "  focus: Install blockers and dates",
  "  labels:", "    - {name: blocker, wake: true}", "    - {name: decision, wake: true}", "    - {name: addressed_to_vexa, wake: true}",
  "  version: 2", "---", "", "# Install review", "", "## Decisions", "", "- Proxy change on Friday", "",
].join("\n");
const segments = [
  { id: "s1", speaker: "Jane", text: "We ship the proxy change on Friday.", t: 0, tsMs: AT, completed: true },
  { id: "s2", speaker: "Ravi", text: "Sounds good.", t: 5, tsMs: AT + 5000, completed: true },
];
const row = { id: ROW, native_id: "abc-defg-hij", session_uid: "uid-1", title: "Install review", when: "", status: "live", live_status: "active",
  platform: "Google Meet", start_time: new Date(AT).toISOString(), participants: [{ name: "Jane" }, { name: "Ravi" }],
  mentioned: [], actions: [], transcript: [], insights: [] };
const puts: { url: string; body: string }[] = [];
let fileBody = PAGE;
let container: HTMLDivElement; let root: Root;
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  container = document.createElement("div"); document.body.appendChild(container);
  puts.length = 0; fileBody = PAGE; sig.asked.length = 0;
  sig.annotations = { s1: { id: "1-0", segment_id: "s1", scores: { decision: 0.93 }, words: { Friday: 0.9 } } };
  try { localStorage.setItem("vexa.meetingTranscriptHeight", "320"); } catch { /* */ }
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = (init?.method ?? "GET").toUpperCase();
    if (url.startsWith("/api/meeting/note")) return json({ path: NOTE, transcript: ROW, cursor: "" });
    if (url.startsWith("/api/workspace/file") && method === "PUT") { puts.push({ url, body: String(init?.body) }); fileBody = JSON.parse(String(init?.body)).content ?? fileBody; return json({ path: NOTE, written: true }); }
    if (url.startsWith("/api/workspace/file")) return json({ content: fileBody });
    return json({ commits: [] });
  }));
});
afterEach(() => { act(() => { root?.unmount(); }); container.remove(); vi.unstubAllGlobals(); setFeatureOverride("all"); });

async function flush() { for (let i = 0; i < 8; i++) await act(async () => { await Promise.resolve(); }); }
async function renderView() {
  meetingsState = [row]; liveState = { transcript: segments };
  root = createRoot(container);
  const services = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  await act(async () => { root.render(<ServicesProvider container={services}><MeetingView meetingId={ROW} /></ServicesProvider>); });
  await flush();
}
const q = (sel: string) => container.querySelector(sel) as HTMLElement | null;

describe("the meeting view with signals on", () => {
  it("shows what Vexa listens for, and chips the line that was a decision", async () => {
    await renderView();
    expect(q("[data-listen-line]")?.textContent).toContain("Install blockers and dates");
    expect(q('[data-signal-chip="decision"]')?.textContent).toBe("Decision");
    expect(q("[data-signal-word]")?.textContent).toBe("Friday");
    // asked for this meeting's signals by its api platform and native id
    expect(sig.asked.at(-1)).toEqual(["Google Meet", "abc-defg-hij", true]);
  });

  it("an edit to the brief is written through the page, with the rest of the page kept", async () => {
    await renderView();
    await act(async () => { fireEvent.click(q("[data-listen-line]")!); });
    const focus = q("[data-listen-focus]") as HTMLTextAreaElement;
    await act(async () => { fireEvent.change(focus, { target: { value: "Dates only" } }); });
    await act(async () => { fireEvent.click(q("[data-listen-save]")!); });
    await flush();
    expect(puts).toHaveLength(1);
    const content = JSON.parse(puts[0].body).content as string;
    expect(content).toContain("focus: Dates only");
    expect(content).toContain("version: 3");
    expect(content).toContain("- Proxy change on Friday");
  });
});

describe("with signals off", () => {
  it("shows no brief and no chips, and asks for no signals", async () => {
    setFeatureOverride({ transcript_signals: false });
    await renderView();
    expect(q("[data-listen-line]")).toBeNull();
    expect(q("[data-signal-chip]")).toBeNull();
    expect(sig.asked.every((a) => (a as unknown[])[2] === false)).toBe(true);
    expect(q("[data-meeting-transcript]")?.textContent).toContain("We ship the proxy change on Friday.");
  });
});
