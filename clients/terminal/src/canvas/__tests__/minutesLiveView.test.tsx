import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

/** PRD decision 34 — "one intelligence". The minutes live view is the RAW transcript and nothing
 *  else. This is the regression fence around the founder's screenshot, which showed, above three
 *  lines of transcript: two model chips, a "Processing on — cleaned + copilot" toggle, and
 *  "Model inference error: no completion endpoint: set VEXA_LLM_BASE_URL".
 *
 *  What must hold, live and completed alike:
 *    - the raw segments stream (speaker · text), from the live feed or the durable store;
 *    - NO processing toggle, no "cleaned + copilot", no "Processed"/"Raw" view switch;
 *    - NO call to the retired arm endpoint (`/api/meeting/process`), on any interaction. */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const durableState: { lines: unknown[] } = { lines: [] };
let meetingsState: unknown[] = [];
let liveState: Record<string, unknown> = {};

const EMPTY_LIVE = { transcript: [], issues: [], connected: false, ended: false, reconnects: 0 };

vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => meetingsState,
  fetchDurableTranscript: vi.fn(async () => ({ lines: durableState.lines })),
}));

vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => ({ ...EMPTY_LIVE, ...liveState }),
}));

import { MeetingCanvasView } from "../MeetingCanvasView";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

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
  durableState.lines = [];
  liveState = {};
});

afterEach(() => {
  act(() => { root?.unmount(); });
  container.remove();
  vi.unstubAllGlobals();
});

async function renderCanvas() {
  root = createRoot(container);
  const services = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  await act(async () => {
    root.render(
      <ServicesProvider container={services}>
        <MeetingCanvasView meetingId="abc-defg-hij" />
      </ServicesProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); });  // flush the durable hydration
}

function processingControls(): Element[] {
  return [...container.querySelectorAll("button[aria-pressed]")];
}

describe("minutes live view — the raw transcript, and nothing else (PRD decision 34)", () => {
  it("a LIVE meeting streams raw segments with no processing controls", async () => {
    meetingsState = [meetingRow(true)];
    liveState = { transcript: [{ id: "s1", speaker: "Jane", text: "we agreed to ship friday", t: 1, completed: true }] };
    await renderCanvas();

    expect(container.textContent).toContain("Jane");
    expect(container.textContent).toContain("we agreed to ship friday");
    expect(processingControls()).toHaveLength(0);
    expect(container.textContent).not.toContain("cleaned + copilot");
    expect(container.textContent).not.toContain("Processing");
    expect(container.textContent).not.toContain("Processed");
  });

  it("a COMPLETED meeting renders the durable segments, still with no view switch", async () => {
    meetingsState = [meetingRow(false)];
    durableState.lines = [LINE];
    await renderCanvas();

    expect(container.textContent).toContain("so um we agreed to ship friday");
    expect(processingControls()).toHaveLength(0);
    expect(container.textContent).not.toContain("cleaned + copilot");
  });

  it("never calls the retired copilot arm endpoint", async () => {
    meetingsState = [meetingRow(true)];
    await renderCanvas();
    for (const btn of [...container.querySelectorAll("button")]) {
      await act(async () => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    }
    const armed = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/meeting/process"));
    expect(armed).toHaveLength(0);
  });

  it("surfaces no model-inference error: the feed carries no such issue kind", async () => {
    meetingsState = [meetingRow(true)];
    liveState = {
      transcript: [{ id: "s1", speaker: "Jane", text: "hello", t: 1, completed: true }],
      // The only issue kinds that exist are feed faults. A "model" kind is not representable.
      issues: [{ kind: "stream", message: "Meeting stream disconnected; reconnecting", at: Date.now() }],
    };
    await renderCanvas();
    expect(container.textContent).not.toContain("Model inference error");
    expect(container.textContent).not.toContain("VEXA_LLM_BASE_URL");
  });
});
