import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

/** Behavioral contract of the transcript-pane toggle (fix/terminal-processed-default-view):
 *  - COMPLETED meeting with persisted processed notes → DEFAULTS to the processed view
 *    (users read the raw default as "my notes are lost").
 *  - COMPLETED meeting without notes → defaults to raw.
 *  - LIVE meeting → unchanged: defaults to raw, and the toggle arms/disarms the copilot.
 *  - COMPLETED meeting → the toggle is a PURE view switch: it must never call /api/meeting/process. */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const durableState: { lines: unknown[]; notes: unknown[] } = { lines: [], notes: [] };
let meetingsState: unknown[] = [];

vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => meetingsState,
  fetchDurableTranscript: vi.fn(async () => ({ lines: durableState.lines, notes: durableState.notes })),
  mergeNotesById: (seed: { id: string }[], live: { id: string }[]) => [...seed, ...live],
}));

vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => ({
    transcript: [], notes: [], cards: [], errors: [], issues: [], note: "",
    connected: false, ended: false, reconnects: 0,
  }),
}));

import { MeetingCanvasView, defaultProcessingView } from "../MeetingCanvasView";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

const NOTE = { id: "n1", speaker: "Jane", text: "We agreed to ship Friday.", t: 1, pass: 1 };
const LINE = { speaker: "Jane", text: "so um we agreed to ship friday", t: 1 };

function meetingRow(live: boolean) {
  return {
    id: "abc-defg-hij", native_id: "abc-defg-hij",
    session_uid: live ? "abc-defg-hij" : undefined,
    title: "Google Meet · abc-defg-hij", when: "", status: live ? "live" : "past",
    platform: "Google Meet", participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  };
}

let container: HTMLDivElement;
let root: Root;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  act(() => { root?.unmount(); });
  container.remove();
  vi.unstubAllGlobals();
});

async function renderCanvas() {
  root = createRoot(container);
  const container2 = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  await act(async () => {
    root.render(
      <ServicesProvider container={container2}>
        <MeetingCanvasView meetingId="abc-defg-hij" />
      </ServicesProvider>,
    );
  });
  // flush the async durable hydration
  await act(async () => { await Promise.resolve(); });
}

function toggleButton(): HTMLButtonElement {
  return container.querySelector("button[aria-pressed]") as HTMLButtonElement;
}

function processCalls(): number {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/meeting/process")).length;
}

describe("defaultProcessingView (pure)", () => {
  it("processed only for completed meetings with notes", () => {
    expect(defaultProcessingView(false, true)).toBe(true);
    expect(defaultProcessingView(false, false)).toBe(false);
    expect(defaultProcessingView(true, true)).toBe(false);
    expect(defaultProcessingView(true, false)).toBe(false);
  });
});

describe("MeetingCanvasView — default view + toggle semantics", () => {
  it("COMPLETED meeting with persisted notes defaults to the PROCESSED view", async () => {
    meetingsState = [meetingRow(false)];
    durableState.lines = [LINE];
    durableState.notes = [NOTE];
    await renderCanvas();
    const btn = toggleButton();
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.textContent).toContain("Processed");
    expect(container.textContent).toContain("cleaned + copilot");
    expect(container.textContent).toContain("We agreed to ship Friday.");
  });

  it("COMPLETED meeting without notes defaults to the RAW view", async () => {
    meetingsState = [meetingRow(false)];
    durableState.lines = [LINE];
    durableState.notes = [];
    await renderCanvas();
    const btn = toggleButton();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(btn.textContent).toContain("Raw");
    expect(container.textContent).toContain("raw transcript");
  });

  it("LIVE meeting keeps the current default (raw / processing off) even when notes exist", async () => {
    meetingsState = [meetingRow(true)];
    durableState.lines = [LINE];
    durableState.notes = [NOTE];
    await renderCanvas();
    const btn = toggleButton();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(btn.textContent).toContain("Processing off");
  });

  it("COMPLETED meeting toggle is a pure view switch — never calls the arm endpoint", async () => {
    meetingsState = [meetingRow(false)];
    durableState.lines = [LINE];
    durableState.notes = [NOTE];
    await renderCanvas();
    const btn = toggleButton();
    await act(async () => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(toggleButton().getAttribute("aria-pressed")).toBe("false"); // flipped to raw
    await act(async () => { toggleButton().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(toggleButton().getAttribute("aria-pressed")).toBe("true"); // back to processed
    expect(processCalls()).toBe(0);
  });

  it("LIVE meeting toggle still arms the copilot via /api/meeting/process", async () => {
    meetingsState = [meetingRow(true)];
    durableState.lines = [];
    durableState.notes = [];
    await renderCanvas();
    await act(async () => { toggleButton().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(toggleButton().getAttribute("aria-pressed")).toBe("true");
    expect(toggleButton().textContent).toContain("Processing on");
    expect(processCalls()).toBe(1);
  });
});
