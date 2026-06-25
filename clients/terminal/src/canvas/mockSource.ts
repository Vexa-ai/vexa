import { MEETINGS, meetingEntities, type MeetingMock, type Participant } from "../surfaces/mock";
import type { CanvasEntity, MeetingState, TranscriptSegment } from "./types";

export type MeetingSourceMode = "live" | "mock";
export type MockInjectKind = "entity" | "number" | "card" | "speaker";
export type MockScenarioId = "sales" | "standup" | "interview";

export interface MockScenario {
  id: MockScenarioId;
  label: string;
  meeting: MeetingMock;
}

interface InjectedMockData {
  entities: CanvasEntity[];
  numbers: { text: string; value?: number }[];
  cards: MeetingState["cards"];
  speakers: Participant[];
  segments: TranscriptSegment[];
}

export interface MockSourceState {
  scenarioId: MockScenarioId;
  cursor: number;
  playing: boolean;
  speed: number;
  injectSeq: number;
  injected: InjectedMockData;
}

const salesFixture = MEETINGS.find((meeting) => meeting.id === "mtg-acme-renewal") ?? MEETINGS[0];
const standupFixture = MEETINGS.find((meeting) => meeting.id === "mtg-standup") ?? MEETINGS[0];

const standupMeeting: MeetingMock = {
  ...standupFixture,
  id: "mock-standup",
  title: "Product engineering standup",
  when: "Now · synthetic",
  status: "live",
  platform: "Zoom",
  participants: [
    { name: "Maya Chen", role: "Engineering manager", initials: "MC" },
    { name: "Owen Brooks", role: "Backend", initials: "OB" },
    { name: "Priya Nair", role: "Frontend", initials: "PN" },
    { name: "You", role: "Product lead", initials: "Y" },
  ],
  mentioned: ["Prepare standup notes", "Recording uploader", "Dashboard polish", "Sprint 18"],
  actions: [
    { id: "standup-a1", label: "Create Sprint 18 risk note", detail: "workspace" },
    { id: "standup-a2", label: "Follow up on uploader logs", detail: "Owen · today" },
    { id: "standup-a3", label: "Pin dashboard QA checklist", detail: "Priya · this afternoon" },
  ],
  transcript: [
    { t: "00:02", speaker: "Maya Chen", text: "Let's do blockers first. The release branch is stable, but recording upload still needs one trace." },
    { t: "00:12", speaker: "Owen Brooks", text: "I have the uploader retry logs. The failing case is a 413 when the chunk grows past 12 MB." },
    { t: "00:24", speaker: "Priya Nair", text: "Dashboard polish is nearly done. I need a final pass on empty states and keyboard focus." },
    { t: "00:36", speaker: "You", text: "I'll take the release note and make sure Sprint 18 calls out the uploader risk clearly." },
    { t: "00:49", speaker: "Maya Chen", text: "Good. If the trace is green by 2 PM, we keep the deploy window today." },
    { t: "01:03", speaker: "Owen Brooks", text: "I'll post the trace with chunk counts, status codes, and the object key before lunch." },
  ],
  insights: [
    { t: "00:14", text: "Risk surfaced — recording upload can fail at 12 MB chunks with HTTP 413." },
    { t: "00:38", text: "Commitment detected — create the Sprint 18 release-risk note." },
    { t: "00:52", text: "Decision point — deploy window stays open if uploader trace is green by 2 PM." },
  ],
};

const interviewMeeting: MeetingMock = {
  ...salesFixture,
  id: "mock-interview",
  title: "Candidate interview — Staff frontend",
  when: "Now · synthetic",
  status: "live",
  platform: "Microsoft Teams",
  participants: [
    { name: "Elena Torres", role: "Candidate", initials: "ET" },
    { name: "Marcus Reed", role: "Hiring manager", initials: "MR" },
    { name: "You", role: "Interviewer", initials: "Y" },
  ],
  mentioned: ["Canvas architecture", "Accessibility", "Design systems", "Next.js"],
  actions: [
    { id: "interview-a1", label: "Summarize frontend strengths", detail: "candidate packet" },
    { id: "interview-a2", label: "Ask for accessibility example", detail: "next question" },
    { id: "interview-a3", label: "Draft interview scorecard", detail: "after call" },
  ],
  transcript: [
    { t: "00:03", speaker: "You", text: "Could you walk us through a frontend system you owned end to end?" },
    { t: "00:16", speaker: "Elena Torres", text: "I led a canvas workflow used by 8,000 weekly users, with strict accessibility and live collaboration requirements." },
    { t: "00:33", speaker: "Marcus Reed", text: "What tradeoff did you make when performance and visual polish pulled in different directions?" },
    { t: "00:47", speaker: "Elena Torres", text: "We measured interaction latency first, then shipped animation only where the 95th percentile stayed under 90 ms." },
    { t: "01:02", speaker: "You", text: "Let's spend five minutes on how you tested keyboard and screen-reader behavior." },
    { t: "01:15", speaker: "Elena Torres", text: "We paired automated axe checks with manual flows for tab order, live regions, and focus restoration." },
  ],
  insights: [
    { t: "00:18", text: "Signal — owned a high-traffic collaborative canvas with accessibility constraints." },
    { t: "00:49", text: "Metric detected — interaction latency p95 under 90 ms." },
    { t: "01:16", text: "Follow-up — ask for a concrete screen-reader debugging story." },
  ],
};

export const MOCK_SCENARIOS: MockScenario[] = [
  { id: "sales", label: "Sales call", meeting: { ...salesFixture, id: "mock-sales", when: "Now · synthetic", status: "live" } },
  { id: "standup", label: "Standup", meeting: standupMeeting },
  { id: "interview", label: "Interview", meeting: interviewMeeting },
];

const injectedEntities: CanvasEntity[] = [
  { kind: "company", title: "Northstar Labs", subtitle: "Company", body: "New account mentioned by the synthetic bench." },
  { kind: "product", title: "Security review", subtitle: "Topic", body: "Follow-up area surfaced during playback." },
  { kind: "person", title: "Avery Stone", subtitle: "New stakeholder", body: "Added by the Mock/Eval bench." },
];

const injectedNumbers = [
  { text: "$42,000", value: 42000 },
  { text: "18%", value: 18 },
  { text: "90 ms", value: 90 },
];

const injectedCards = [
  { kind: "eval", title: "Synthetic risk surfaced", body: "The bench injected a card so the generated view can react immediately." },
  { kind: "eval", title: "Decision marker", body: "A visible mock decision was added to the meeting card stream." },
  { kind: "eval", title: "Follow-up prompt", body: "The view should now show one more actionable card." },
];

const injectedSpeakers: Participant[] = [
  { name: "Sam Rivera", role: "Observer", initials: "SR" },
  { name: "Taylor Kim", role: "Customer success", initials: "TK" },
  { name: "Nina Walsh", role: "Finance", initials: "NW" },
];

const injectedSpeakerLines = [
  "I am joining late, but the key point is visible in the canvas now.",
  "Please add me to the follow-up so this shows up in speaker summaries.",
  "The number is right, and I want the next step captured before the call ends.",
];

function emptyInjected(): InjectedMockData {
  return { entities: [], numbers: [], cards: [], speakers: [], segments: [] };
}

function scenarioFor(id: MockScenarioId): MockScenario {
  return MOCK_SCENARIOS.find((scenario) => scenario.id === id) ?? MOCK_SCENARIOS[0];
}

function initialCursor(meeting: MeetingMock): number {
  return Math.min(Math.max(meeting.transcript.length, 0), 2);
}

function toSegment(line: { t?: string; speaker?: string; text: string }): TranscriptSegment {
  return { speaker: line.speaker ?? "Speaker", text: line.text, ts: line.t };
}

function extractNumbers(texts: string[]): { text: string; value?: number }[] {
  const out: { text: string; value?: number }[] = [];
  const re = /(?:[$€£]\s*)?\b\d[\d,]*(?:\.\d+)?(?:\s?ms|%)?\b/g;
  for (const text of texts) {
    const matches = text.match(re) ?? [];
    for (const raw of matches) {
      const cleaned = raw.replace(/[^0-9.-]/g, "");
      const value = cleaned ? Number(cleaned) : undefined;
      out.push({ text: raw, value: Number.isFinite(value) ? value : undefined });
    }
  }
  return out.slice(-16);
}

function splitInjectedEntities(items: CanvasEntity[]): Pick<MeetingState["entities"], "people" | "companies" | "products"> {
  return {
    people: items.filter((item) => item.kind === "person"),
    companies: items.filter((item) => item.kind === "company"),
    products: items.filter((item) => item.kind === "product"),
  };
}

export function createMockSourceState(scenarioId: MockScenarioId = "sales"): MockSourceState {
  const scenario = scenarioFor(scenarioId);
  return {
    scenarioId: scenario.id,
    cursor: initialCursor(scenario.meeting),
    playing: false,
    speed: 1,
    injectSeq: 0,
    injected: emptyInjected(),
  };
}

export function setMockScenario(state: MockSourceState, scenarioId: MockScenarioId): MockSourceState {
  return { ...createMockSourceState(scenarioId), speed: state.speed };
}

export function resetMockSource(state: MockSourceState): MockSourceState {
  return { ...createMockSourceState(state.scenarioId), speed: state.speed };
}

export function setMockPlaying(state: MockSourceState, playing: boolean): MockSourceState {
  const scenario = scenarioFor(state.scenarioId);
  return { ...state, playing: playing && state.cursor < scenario.meeting.transcript.length };
}

export function setMockSpeed(state: MockSourceState, speed: number): MockSourceState {
  const next = Number.isFinite(speed) ? speed : 1;
  return { ...state, speed: Math.min(4, Math.max(0.5, next)) };
}

export function stepMockSource(state: MockSourceState): MockSourceState {
  const scenario = scenarioFor(state.scenarioId);
  const nextCursor = Math.min(scenario.meeting.transcript.length, state.cursor + 1);
  return { ...state, cursor: nextCursor, playing: state.playing && nextCursor < scenario.meeting.transcript.length };
}

export function injectMockItem(state: MockSourceState, kind: MockInjectKind): MockSourceState {
  const injectSeq = state.injectSeq + 1;
  const index = state.injectSeq % 3;
  if (kind === "entity") {
    return {
      ...state,
      injectSeq,
      injected: { ...state.injected, entities: [...state.injected.entities, injectedEntities[index]] },
    };
  }
  if (kind === "number") {
    return {
      ...state,
      injectSeq,
      injected: { ...state.injected, numbers: [...state.injected.numbers, injectedNumbers[index]] },
    };
  }
  if (kind === "card") {
    return {
      ...state,
      injectSeq,
      injected: {
        ...state.injected,
        cards: [...state.injected.cards, { id: `mock-card-${injectSeq}`, ...injectedCards[index] }],
      },
    };
  }
  const speaker = injectedSpeakers[index];
  return {
    ...state,
    injectSeq,
    injected: {
      ...state.injected,
      speakers: [...state.injected.speakers, speaker],
      segments: [
        ...state.injected.segments,
        { speaker: speaker.name, text: injectedSpeakerLines[index], ts: `+${injectSeq}` },
      ],
    },
  };
}

export function buildMockMeetingState(state: MockSourceState): MeetingState {
  const scenario = scenarioFor(state.scenarioId);
  const meeting = scenario.meeting;
  const transcriptSegments = meeting.transcript.slice(0, state.cursor).map(toSegment);
  const segments = [...transcriptSegments, ...state.injected.segments];
  const revealedCards = Math.max(1, Math.floor((state.cursor + 1) / 2));
  const cards: MeetingState["cards"] = [
    ...meeting.insights.slice(0, revealedCards).map((card, index) => ({ id: `mock-insight-${index}`, kind: "insight", title: card.text, ts: card.t })),
    ...meeting.actions.slice(0, Math.max(0, state.cursor - 2)).map((action) => ({ id: action.id, kind: "action", title: action.label, body: action.detail })),
    ...state.injected.cards,
  ];
  const { present, detected } = meetingEntities({ ...meeting, participants: [...meeting.participants, ...state.injected.speakers] });
  const revealedEntities = Math.max(1, Math.ceil(state.cursor / 2));
  const injected = splitInjectedEntities(state.injected.entities);
  const companies = [
    ...detected.filter((entity) => entity.type === "company").slice(0, revealedEntities),
    ...injected.companies,
  ];
  const products = [
    ...detected.filter((entity) => entity.type === "topic" || entity.type === "task").slice(0, revealedEntities + 1),
    ...injected.products,
  ];
  const textCorpus = [...segments.map((segment) => segment.text), ...cards.flatMap((card) => [card.title, card.body ?? ""])];
  const numbers = [...extractNumbers(textCorpus), ...state.injected.numbers];
  const lastSegment = segments[segments.length - 1];

  return {
    meeting: {
      id: meeting.id,
      title: meeting.title,
      startedAt: meeting.scheduled_at,
      participants: [...meeting.participants, ...state.injected.speakers].map((participant) => participant.name),
    },
    transcript: {
      segments,
      liveCaption: state.playing ? lastSegment?.text : undefined,
    },
    entities: {
      people: [...present, ...injected.people],
      companies,
      products,
      numbers,
    },
    cards,
    metrics: {
      source: "mock",
      scenario: scenario.label,
      participants: meeting.participants.length + state.injected.speakers.length,
      cards: cards.length,
      transcriptSegments: segments.length,
      playbackPct: meeting.transcript.length ? Math.round((state.cursor / meeting.transcript.length) * 100) : 100,
    },
    sections: {
      mock: {
        scenario: scenario.id,
        playing: state.playing,
        speed: state.speed,
      },
    },
  };
}

export function meetingStateHasData(state: MeetingState): boolean {
  return Boolean(
    state.transcript.segments.length ||
    state.cards.length ||
    state.entities.people.length ||
    state.entities.companies.length ||
    state.entities.products.length ||
    state.entities.numbers.length
  );
}
