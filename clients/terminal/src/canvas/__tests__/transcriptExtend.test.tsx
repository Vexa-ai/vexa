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

/** the whole gesture as the reader performs it: highlight, press, then a key. */
function extend(phrase: string, line?: string) {
  expect(highlight(container, phrase)).toBe(true);
  fireEvent.mouseDown(control() as HTMLElement);
  const input = field() as HTMLInputElement;
  if (line === undefined) { fireEvent.keyDown(input, { key: "Escape" }); return; }
  fireEvent.change(input, { target: { value: line } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("the Extend control on a transcript selection", () => {
  it("is not on screen until something is selected", async () => {
    await renderRoom();
    expect(container.textContent).toContain(SAID);
    expect(control()).toBeNull();

    highlight(container, PASSAGE);
    expect(control()).toBeTruthy();
  });

  it("is the same control a page has — a press opens the line, and sends nothing yet", async () => {
    await renderRoom();
    highlight(container, PASSAGE);
    fireEvent.mouseDown(control() as HTMLElement);

    expect(field()).toBeTruthy();
    expect(field()!.placeholder).toBe(LINE_PLACEHOLDER);
    expect(asks).toHaveLength(0);                       // the press is not the act (#1593)
  });

  it("fires an act carrying the words, the meeting and where in the room they were said", async () => {
    await renderRoom();
    extend(PASSAGE);

    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual({
      kind: "extend_transcript", meeting: MEETING, selection: PASSAGE, ...WHERE,
    });
  });

  it("carries the person's own line when they typed one, verbatim", async () => {
    await renderRoom();
    extend(PASSAGE, LINE);

    expect(asks[0].intent).toEqual({
      kind: "extend_transcript", meeting: MEETING, selection: PASSAGE, ...WHERE, instruction: LINE,
    });
    expect(asks[0].prompt).toContain(INSTRUCTION_LEAD);
    expect(asks[0].prompt).toContain(LINE);
  });

  it("shows the reader the label, never the prompt", async () => {
    await renderRoom();
    extend(PASSAGE, LINE);

    expect(asks[0].display).toBe(`Extend: meeting ${MEETING} · “${PASSAGE}”`);
    expect(asks[0].display).not.toContain(LINE);        // the bubble stays the act label (#1588)
    expect(asks[0].prompt).toContain(`meeting ${MEETING}`);
  });

  it("leaves the transcript exactly as it was — the act writes pages, never the record", async () => {
    await renderRoom();
    const before = container.querySelector("main")?.textContent;

    extend(PASSAGE);

    expect(container.querySelector("main")?.textContent).toBe(before);
    expect(container.querySelector("main")?.textContent).toContain(SAID);
  });

  it("navigates nowhere on the reply — the pages it writes have paths nobody can predict", async () => {
    await renderRoom();
    extend(PASSAGE);
    expect(pendingLanding()).toBeNull();
  });

  it("says nothing about where a passage the room said twice was said", async () => {
    await renderRoom([
      { id: "s1", speaker: "Jane", text: "let us park that", tsMs: AT, completed: true },
      { id: "s2", speaker: "Ravi", text: "let us park that", tsMs: AT + 5000, completed: true },
    ]);
    extend("let us park that");

    expect(asks[0].intent).toEqual({
      kind: "extend_transcript", meeting: MEETING, selection: "let us park that",
    });
  });

  it("a selection somewhere else on the screen is not this transcript's", async () => {
    await renderRoom();
    const elsewhere = document.createElement("p");
    elsewhere.textContent = "a sentence in the chat, not in the room";
    document.body.appendChild(elsewhere);
    highlight(elsewhere, "a sentence in the chat");

    expect(control()).toBeNull();
    expect(asks).toHaveLength(0);
    elsewhere.remove();
  });

  it("closes on the act — the highlight is spent", async () => {
    await renderRoom();
    extend(PASSAGE);

    expect(control()).toBeNull();
    expect(field()).toBeNull();
    expect(asks).toHaveLength(1);
  });
});
