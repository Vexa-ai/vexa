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

const salesMeeting: MeetingMock = {
  ...salesFixture,
  id: "mock-sales",
  native_id: "acme-discovery-call",
  title: "Acme · discovery call",
  when: "Now · synthetic",
  status: "live",
  live_status: "active",
  scheduled_at: "2026-06-25T13:05:00Z",
  platform: "Google Meet",
  participants: [
    { name: "Jane Liu", role: "CRO · Acme", initials: "JL" },
    { name: "Raj Patel", role: "CTO · Acme", initials: "RP" },
    { name: "Mara Gomez", role: "RevOps · Acme", initials: "MG" },
    { name: "You", role: "Account lead", initials: "Y" },
  ],
  mentioned: ["Acme Corp", "Snowflake", "Vexa", "Q3 rollout", "$50k pilot", "200 seats"],
  actions: [
    { id: "sales-a1", label: "Send mutual action plan", detail: "next step · due Friday" },
    { id: "sales-a2", label: "Research Snowflake comparison", detail: "workspace KG + web" },
    { id: "sales-a3", label: "Draft Q3 pilot proposal", detail: "$50k · 200 seats" },
  ],
  transcript: [
    { t: "00:03", speaker: "You", text: "Thanks for joining. I saw Acme is standardizing revenue workflows ahead of the Q3 rollout." },
    { t: "00:18", speaker: "Jane Liu", text: "Exactly. If this helps reps prepare faster, we can start with a $50k pilot and expand after Q3." },
    { t: "00:34", speaker: "Raj Patel", text: "The blocker is security. Snowflake already has our warehouse data, so we need a clear answer on what Vexa stores." },
    { t: "00:52", speaker: "Mara Gomez", text: "For the pilot, assume 200 seats across enterprise sales and customer success." },
    { t: "01:07", speaker: "You", text: "I will send a mutual action plan with security notes, the Snowflake comparison, and a 200-seat rollout model." },
    { t: "01:24", speaker: "Jane Liu", text: "Good. If Raj is comfortable by next Friday, I can sponsor the Q3 pilot in our operating review." },
    { t: "01:42", speaker: "Raj Patel", text: "Add data retention and SSO to that plan. If those are clean, I will unblock procurement." },
    { t: "01:58", speaker: "Mara Gomez", text: "And please include rep onboarding. The team needs this live before the July pipeline review." },
  ],
  insights: [
    { t: "00:35", text: "Objection — Raj compared Vexa to Snowflake and needs a data-storage answer before procurement." },
    { t: "01:08", text: "Commitment — send the mutual action plan with security notes, Snowflake comparison, and rollout model." },
    { t: "01:25", text: "Next step — Jane will sponsor the Q3 pilot if Raj is comfortable by next Friday." },
  ],
  docs: [
    { workspace: "u_live", path: "kg/entities/meeting/acme-discovery-call.md", title: "Acme discovery brief", kind: "brief" },
  ],
};

const salesPeople = [
  { kind: "person", title: "Jane Liu", name: "Jane Liu", context: "Buyer", subtitle: "CRO · Acme", body: "Economic buyer for the Q3 pilot; can sponsor the operating-review ask.", quote: "I can sponsor the Q3 pilot in our operating review." },
  { kind: "person", title: "Raj Patel", name: "Raj Patel", context: "Security", subtitle: "CTO · Acme", body: "Technical approver; focused on storage, retention, SSO, and procurement unblock.", quote: "If those are clean, I will unblock procurement." },
  { kind: "person", title: "Mara Gomez", name: "Mara Gomez", context: "Ops", subtitle: "RevOps · Acme", body: "Owns rollout logistics and seat planning for the pilot group.", quote: "Assume 200 seats across enterprise sales and customer success." },
];

const salesCompanies = [
  { kind: "company", title: "Acme Corp", name: "Acme Corp", context: "Buyer", path: "kg/entities/company/acme-corp.md", exists: true, summary: "Enterprise account evaluating Vexa for a Q3 revenue-workflow pilot.", quote: "Acme is standardizing revenue workflows ahead of the Q3 rollout." },
  { kind: "company", title: "Snowflake", name: "Snowflake", context: "Competitor", path: "kg/entities/company/snowflake.md", exists: true, summary: "Existing warehouse vendor and comparison anchor for Acme's security review.", quote: "Snowflake already has our warehouse data, so we need a clear answer on what Vexa stores." },
];

const salesProducts = [
  { kind: "product", title: "Vexa", name: "Vexa", context: "Platform", summary: "Meeting intelligence and workspace KG surface under evaluation for the Q3 pilot.", quote: "We need a clear answer on what Vexa stores." },
];

const salesNumbers = [
  { kind: "number", title: "$50k", text: "$50k", name: "$50k", context: "Budget", quote: "We can start with a $50k pilot and expand after Q3." },
  { kind: "number", title: "Q3", text: "Q3", name: "Q3", context: "Timeline", quote: "I can sponsor the Q3 pilot in our operating review." },
  { kind: "number", title: "200 seats", text: "200 seats", name: "200 seats", context: "Seats", quote: "Assume 200 seats across enterprise sales and customer success." },
];

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
  { id: "sales", label: "Acme discovery", meeting: salesMeeting },
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

function cardKindFromText(text: string, fallback = "insight"): string {
  const lower = text.toLowerCase();
  if (lower.includes("objection") || lower.includes("concern") || lower.includes("risk")) return "objection";
  if (lower.includes("commitment") || lower.includes("committed") || lower.includes("will ")) return "commitment";
  if (lower.includes("next step") || lower.includes("follow-up") || lower.includes("follow up")) return "next-step";
  if (lower.includes("action") || lower.includes("task")) return "action";
  return fallback;
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
    ...meeting.insights.slice(0, revealedCards).map((card, index) => ({ id: `mock-insight-${index}`, kind: cardKindFromText(card.text), title: card.text, ts: card.t })),
    ...meeting.actions.slice(0, Math.max(0, state.cursor - 2)).map((action) => ({ id: action.id, kind: "action", title: action.label, body: action.detail })),
    ...state.injected.cards,
  ];
  const { present, detected } = meetingEntities({ ...meeting, participants: [...meeting.participants, ...state.injected.speakers] });
  const revealedEntities = state.scenarioId === "sales" ? 99 : Math.max(1, Math.ceil(state.cursor / 2));
  const injected = splitInjectedEntities(state.injected.entities);
  const seededPeople = state.scenarioId === "sales" ? salesPeople : [];
  const seededCompanies = state.scenarioId === "sales" ? salesCompanies : [];
  const seededProducts = state.scenarioId === "sales" ? salesProducts : [];
  const seededNumbers = state.scenarioId === "sales" ? salesNumbers : [];
  const companies = [
    ...seededCompanies,
    ...detected.filter((entity) => entity.type === "company").slice(0, revealedEntities),
    ...injected.companies,
  ];
  const products = [
    ...seededProducts,
    ...detected.filter((entity) => entity.type === "topic" || entity.type === "task").slice(0, revealedEntities + 1),
    ...injected.products,
  ];
  const textCorpus = [...segments.map((segment) => segment.text), ...cards.flatMap((card) => [card.title, card.body ?? ""])];
  const numbers = [...seededNumbers, ...extractNumbers(textCorpus), ...state.injected.numbers];
  const lastSegment = segments[segments.length - 1];

  return {
    meeting: {
      id: meeting.id,
      nativeId: meeting.native_id,
      title: meeting.title,
      status: meeting.live_status ?? meeting.status,
      startedAt: meeting.scheduled_at,
      participants: [...meeting.participants, ...state.injected.speakers].map((participant) => participant.name),
      docs: (meeting.docs ?? []).map((doc) => ({
        path: doc.path,
        title: doc.title,
        kind: doc.kind,
        present: true,
      })),
    },
    transcript: {
      segments,
      liveCaption: state.playing ? lastSegment?.text : undefined,
    },
    entities: {
      people: [...present, ...seededPeople, ...injected.people],
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
