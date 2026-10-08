/** EXTEND ON A TRANSCRIPT SELECTION (Vexa-ai/vexa#1596). Founder, 2026-09-06, in a live meeting with
 *  the canvas open: *"we also want extend on transcript when i can select some text and push the
 *  button"*.
 *
 *  Driven through `MeetingCanvasView` and not through the control alone, for the reason
 *  `extendPanel.test.tsx` gives about the pages panel: the claims that matter are about the SCREEN.
 *  That the control appears over a selection in the transcript and only there, that it is the same
 *  control a page has — the one-line field of #1593 included — that the act carries the meeting and
 *  where in the room the words were said, and that pressing it leaves the transcript exactly as it
 *  was: none of those is observable from a button in isolation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent } from "@testing-library/react";

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
import { ASK_CHAT_EVENT } from "../actions";
import { LINE_PLACEHOLDER } from "../../minutes/ExtendAction";
import { INSTRUCTION_LEAD, clearPending, pendingLanding } from "../../minutes/extend";
import { resetActs } from "../../surfaces/actState";
import { ServicesProvider, createContainer, reg } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import type { ChatIntent } from "../../surfaces/chatIntent";

const MEETING = "abc-defg-hij";
const AT = Date.UTC(2026, 8, 6, 11, 52, 0);
const SAID = "their pilot ships in March, self-hosted";
const PASSAGE = "pilot ships in March";
const LINE = "check whether that date is public anywhere";

const meetingRow = () => ({
  id: MEETING, native_id: MEETING, session_uid: MEETING,
  title: "Google Meet · abc-defg-hij", when: "", status: "live",
  platform: "Google Meet", participants: [], mentioned: [], actions: [], transcript: [], insights: [],
});

const ROOM = [
  { id: "s1", speaker: "Jane", text: "we looked at Kaar Tech last week", tsMs: AT, completed: true },
  { id: "s2", speaker: "Ravi", text: SAID, tsMs: AT + 9000, completed: true },
];

const WHERE = { segment: "s2", speaker: "Ravi", at: new Date(AT + 9000).toISOString() };

const asks: { prompt?: string; display?: string; intent?: ChatIntent }[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  durableState.lines = [];
  liveState = {};
  asks.length = 0;
  clearPending();
  resetActs();   // the act store outlives an unmount — one test's running act is not the next one's
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
});

afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  act(() => { root?.unmount(); });
  container.remove();
  vi.unstubAllGlobals();
});

async function renderRoom(transcript: unknown[] = ROOM) {
  meetingsState = [meetingRow()];
  liveState = { transcript };
  root = createRoot(container);
  const services = createContainer([reg(LayoutServiceId, () => createLayoutService("meetings"))]);
  await act(async () => {
    root.render(
      <ServicesProvider container={services}>
        <MeetingCanvasView meetingId={MEETING} />
      </ServicesProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); });   // flush the durable hydration
}

/** Highlight `phrase` wherever it is rendered — the reader drags across words, not across nodes. */
function highlight(host: HTMLElement, phrase: string): boolean {
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const i = (n.textContent ?? "").indexOf(phrase);
    if (i < 0) continue;
    const range = document.createRange();
    range.setStart(n, i);
    range.setEnd(n, i + phrase.length);
    const sel = window.getSelection() as Selection;
    sel.removeAllRanges();
    sel.addRange(range);
    act(() => { document.dispatchEvent(new Event("selectionchange")); });
    return true;
  }
  return false;
}

const control = () => container.querySelector('[data-doc-act="extend-transcript"]') as HTMLElement | null;
const field = () => container.querySelector("[data-act-field]") as HTMLInputElement | null;

/** The ROOM'S OWN WORDS, with the control that floats over them taken out. The control is inside the
 *  transcript's box — it has to be, it points at a passage — and since Vexa-ai/vexa#1604 it stays
 *  there while its act runs, saying what the act is doing. That text is the ACT reporting itself,
 *  never the record, and the claim below is about the record. */
const roomText = () => {
  const main = container.querySelector("main")?.cloneNode(true) as HTMLElement | undefined;
  main?.querySelector('[data-doc-act="extend-transcript"]')?.remove();
  return main?.textContent;
};

describe("Ask about this on transcript selections", () => {
  it("prepares the quote and meeting reference without sending or changing the transcript", async () => {
    await renderRoom();
    const before = roomText();
    expect(control()).toBeNull();
    expect(highlight(container, PASSAGE)).toBe(true);
    expect(control()?.textContent).toContain("Ask about this");
    fireEvent.click(control() as HTMLElement);
    expect(asks).toHaveLength(1);
    expect(asks[0]).toMatchObject({ mode: "draft", reference: { meeting: MEETING, ...WHERE } });
    expect(asks[0].intent).toBeUndefined();
    expect(asks[0].prompt).toContain(`> ${PASSAGE}`);
    expect(roomText()).toBe(before);
    expect(control()).toBeNull();
    expect(pendingLanding()).toBeNull();
  });

  it("ignores selections outside the transcript", async () => {
    await renderRoom();
    const elsewhere = document.createElement("p");
    elsewhere.textContent = "another pane";
    document.body.appendChild(elsewhere);
    highlight(elsewhere, "another pane");
    expect(control()).toBeNull();
    expect(asks).toHaveLength(0);
    elsewhere.remove();
  });
});
