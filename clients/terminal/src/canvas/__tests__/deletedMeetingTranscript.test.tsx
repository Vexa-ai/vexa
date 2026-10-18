import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

/** After a meeting's transcript and recording are deleted, a transcript view that is already open
 *  switches to the deleted state as soon as the meetings list carries the deletion — it does not
 *  keep showing what it fetched or streamed before, and it shows no transcript after a reload. */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const durableState: { lines: unknown[] } = { lines: [] };
let meetingsState: unknown[] = [];
let liveState: Record<string, unknown> = {};
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

import { MeetingCanvasView } from "../MeetingCanvasView";
import { MeetingTranscriptWidget } from "../TranscriptWidget";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

const SAID = "the budget is final";
const DELETED_LABEL = "This meeting’s transcript and recording were deleted.";

function row(deleted: boolean) {
  return {
    id: "41", native_id: "abc-defg-hij", title: "Budget", when: "", status: "past",
    live_status: "completed", platform: "Google Meet",
    participants: [], mentioned: [], actions: [], transcript: [], insights: [],
    ...(deleted ? { artifacts_deleted: true } : {}),
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  durableState.lines = [{ speaker: "Ana", text: SAID, t: 1 }];
  liveState = {};
});

afterEach(() => {
  act(() => { root?.unmount(); });
  container.remove();
  vi.unstubAllGlobals();
});

async function show(view: "widget" | "canvas") {
  const services = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  const el = (
    <ServicesProvider container={services}>
      {view === "widget" ? <MeetingTranscriptWidget meetingId="41" /> : <MeetingCanvasView meetingId="41" />}
    </ServicesProvider>
  );
  await act(async () => { root.render(el); });
  await act(async () => { await Promise.resolve(); });   // flush the durable hydration
}

describe.each(["widget", "canvas"] as const)("a deleted meeting's transcript (%s)", (view) => {
  it("an open view switches to the deleted state when the list carries the deletion", async () => {
    root = createRoot(container);
    meetingsState = [row(false)];
    await show(view);
    expect(container.textContent).toContain(SAID);

    meetingsState = [row(true)];          // what refreshMeetings() brings back after the delete
    await show(view);
    expect(container.textContent).not.toContain(SAID);
    expect(container.textContent).toContain(DELETED_LABEL);
  });

  it("a deleted meeting opened fresh shows the deleted state, not an empty live room", async () => {
    root = createRoot(container);
    meetingsState = [row(true)];
    await show(view);
    expect(container.textContent).not.toContain(SAID);
    expect(container.textContent).toContain(DELETED_LABEL);
    expect(container.textContent).not.toContain("this fills in as the room talks");
  });
});
