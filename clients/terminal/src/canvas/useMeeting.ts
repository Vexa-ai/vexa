"use client";
import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLiveMeetings, fetchTranscript } from "../surfaces/liveMeetings";
import { meetingById, meetingEntities, type MeetingMock, type TranscriptLine } from "../surfaces/mock";
import { useMeetingLive } from "../surfaces/meetingLive";
import { useCanvasActionState } from "./actions";
import {
  MOCK_SCENARIOS,
  buildMockMeetingState,
  createMockSourceState,
  injectMockItem,
  meetingStateHasData,
  resetMockSource,
  setMockPlaying,
  setMockScenario,
  setMockSpeed,
  stepMockSource,
  type MeetingSourceMode,
  type MockInjectKind,
  type MockScenarioId,
  type MockSourceState,
} from "./mockSource";
import { cleanTranscriptText, extractNotableNumbers } from "./textSignals";
import type { EntityItem, EntityKind, MeetingDocLink, MeetingState, SpeakerSummary, TranscriptSegment } from "./types";

export type { MeetingSourceMode, MockInjectKind, MockScenarioId } from "./mockSource";

const EMPTY_MEETING: MeetingMock = {
  id: "u_live",
  title: "Live meeting",
  when: "",
  status: "past",
  platform: "Meeting",
  participants: [],
  mentioned: [],
  actions: [],
  transcript: [],
  insights: [],
};

const MeetingScopeContext = createContext<string | undefined>(undefined);

export function MeetingScopeProvider({ meetingId, children }: { meetingId?: string; children: ReactNode }) {
  return createElement(MeetingScopeContext.Provider, { value: meetingId }, children);
}

interface MeetingSourceControls {
  play(): void;
  pause(): void;
  step(): void;
  reset(): void;
  setScenario(scenarioId: MockScenarioId): void;
  setSpeed(speed: number): void;
  inject(kind: MockInjectKind): void;
}

interface MeetingSourceContextValue {
  state: MeetingState;
  live: MeetingState;
  mock: MeetingState;
  mode: MeetingSourceMode;
  activeMode: MeetingSourceMode;
  liveHasData: boolean;
  scenarios: { id: MockScenarioId; label: string }[];
  mockState: MockSourceState;
  setMode(mode: MeetingSourceMode): void;
  controls: MeetingSourceControls;
}

const MeetingSourceContext = createContext<MeetingSourceContextValue | null>(null);
const DEFAULT_MEETING_STATE = buildMockMeetingState(createMockSourceState());

function pickMeeting(meetings: MeetingMock[]): MeetingMock {
  return meetings.find((m) => m.status === "live") ?? meetings[0] ?? EMPTY_MEETING;
}

function matchesMeeting(m: MeetingMock, meetingId: string): boolean {
  return m.id === meetingId || m.native_id === meetingId;
}

function unresolvedMeeting(meetingId: string): MeetingMock {
  return { ...EMPTY_MEETING, id: meetingId, title: "Meeting" };
}

function resolveMeeting(meetings: MeetingMock[], meetingId: string): MeetingMock {
  // A real meeting id must NEVER resolve to a MOCK meeting. If it isn't in the live+past list yet,
  // show an empty placeholder for THAT id (it fills in when the list loads) — not a wrong meeting.
  return meetings.find((m) => matchesMeeting(m, meetingId)) ?? unresolvedMeeting(meetingId);
}

function latestCaption(segments: { text: string; completed?: boolean }[], note: string): string | undefined {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim() && seg.completed === false) return cleanTranscriptText(seg.text);
  }
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim()) return cleanTranscriptText(seg.text);
  }
  const cleanNote = cleanTranscriptText(note);
  return cleanNote || undefined;
}

function cardKindFromText(text: string, fallback = "insight"): string {
  const lower = text.toLowerCase();
  if (lower.includes("objection") || lower.includes("concern") || lower.includes("risk")) return "objection";
  if (lower.includes("commitment") || lower.includes("committed") || lower.includes("will ")) return "commitment";
  if (lower.includes("next step") || lower.includes("follow-up") || lower.includes("follow up")) return "next-step";
  if (lower.includes("action") || lower.includes("task")) return "action";
  return fallback;
}

function lineTs(line: TranscriptLine): string | undefined {
  return line.t || undefined;
}

function safeArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function textOf(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (value == null) return fallback;
  return String(value);
}

function numberOf(value: unknown): number | undefined {
  const next = typeof value === "number" ? value : Number(value);
  return Number.isFinite(next) ? next : undefined;
}

function field(source: unknown, key: string): unknown {
  return source && typeof source === "object" ? (source as Record<string, unknown>)[key] : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function parseTimestampMs(value: number | string | undefined): number | undefined {
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value !== "string") return undefined;
  const raw = value.trim();
  if (!raw) return undefined;
  const parts = raw.split(":").map((part) => Number(part));
  if (parts.some((part) => !Number.isFinite(part))) return undefined;
  if (parts.length === 3) return ((parts[0] * 60 * 60) + (parts[1] * 60) + parts[2]) * 1000;
  if (parts.length === 2) return ((parts[0] * 60) + parts[1]) * 1000;
  return numberOf(raw);
}

function estimateTalkMs(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  if (!words) return 0;
  return Math.max(1200, Math.round((words / 150) * 60_000));
}

function normalizeSegments(segments: TranscriptSegment[] | null | undefined): TranscriptSegment[] {
  return safeArray(segments)
    .map((segment) => ({
      speaker: textOf(segment.speaker, "Speaker"),
      text: cleanTranscriptText(textOf(segment.text)),
      ts: segment.ts,
    }))
    .filter((segment) => segment.text.trim());
}

function entityTitle(item: unknown, fallback: string): string {
  if (typeof item === "string") return cleanTranscriptText(item);
  return cleanTranscriptText(textOf(field(item, "title") ?? field(item, "name") ?? field(item, "text") ?? field(item, "value"), fallback));
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "unknown";
}

function normalizeSearchText(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9$€£%]+/g, " ").replace(/\s+/g, " ").trim();
}

function oneWord(value: unknown, fallback: string): string {
  const raw = textOf(value).replace(/[\[\](){}]/g, " ").split(/[·:|,\s/]+/).filter(Boolean)[0];
  return raw || fallback;
}

function numberContext(name: string): string {
  const lower = name.toLowerCase();
  if (/[$€£]/.test(name) || /\bk\b/.test(lower)) return "Budget";
  if (/\bq[1-4]\b|quarter|timeline|july|august|september|october|november|december/.test(lower)) return "Timeline";
  if (/seat|user|license/.test(lower)) return "Seats";
  if (/%/.test(name)) return "Rate";
  return "Number";
}

function entityName(kind: EntityKind, item: unknown, index: number): string {
  if (kind === "number" && isRecord(item)) return textOf(field(item, "text") ?? field(item, "name") ?? field(item, "title") ?? field(item, "value"), `number ${index + 1}`);
  return entityTitle(item, `${kind} ${index + 1}`);
}

function entitySummary(item: unknown): string {
  return cleanTranscriptText(textOf(field(item, "summary") ?? field(item, "body") ?? field(item, "detail")));
}

function directDocPath(item: unknown): string {
  return textOf(field(item, "docPath") ?? field(item, "path"));
}

function firstQuoteFor(name: string, segments: TranscriptSegment[]): string | undefined {
  const needle = normalizeSearchText(name);
  if (!needle) return undefined;
  const compactNeedle = needle.replace(/\s+/g, "");
  for (const segment of segments) {
    const text = textOf(segment.text);
    const speaker = textOf(segment.speaker);
    const haystack = normalizeSearchText(`${speaker} ${text}`);
    if (haystack.includes(needle) || haystack.replace(/\s+/g, "").includes(compactNeedle)) return cleanTranscriptText(text);
  }
  return undefined;
}

function contextForEntity(kind: EntityKind, item: unknown, name: string): string {
  if (kind === "number") return oneWord(field(item, "context"), numberContext(name));
  if (kind === "signal") return oneWord(field(item, "context") ?? field(item, "kind"), "Signal");
  return oneWord(field(item, "context") ?? field(item, "subtitle") ?? field(item, "role") ?? field(item, "type"), kind[0].toUpperCase() + kind.slice(1));
}

function enrichEntity(kind: EntityKind, item: unknown, index: number, segments: TranscriptSegment[]): EntityItem {
  const name = entityName(kind, item, index);
  const summary = entitySummary(item);
  const directPath = directDocPath(item);
  const docPath = directPath || `kg/entities/${kind}/${slug(name)}.md`;
  const exists = field(item, "exists");
  const researched = typeof exists === "boolean" ? exists : Boolean(directPath || summary);
  const quote = textOf(field(item, "quote")) || firstQuoteFor(name, segments);
  return {
    id: `${kind}:${slug(name)}:${index}`,
    kind,
    name,
    context: contextForEntity(kind, item, name),
    summary: summary || textOf(field(item, "subtitle") ?? field(item, "role")),
    quote,
    docPath,
    researched,
    title: name,
    subtitle: textOf(field(item, "subtitle") ?? field(item, "role"), undefined),
    body: summary,
    value: numberOf(field(item, "value")) ?? textOf(field(item, "value"), undefined),
  };
}

function mergeEntityItems(items: EntityItem[]): EntityItem[] {
  const merged = new Map<string, EntityItem>();
  for (const item of items) {
    const key = `${item.kind}:${slug(item.name)}`;
    const prev = merged.get(key);
    if (!prev) {
      merged.set(key, item);
      continue;
    }
    merged.set(key, {
      ...prev,
      ...item,
      context: item.context || prev.context,
      summary: item.summary && item.summary.length > (prev.summary?.length ?? 0) ? item.summary : prev.summary || item.summary,
      quote: item.quote && item.quote.length > (prev.quote?.length ?? 0) ? item.quote : prev.quote || item.quote,
      docPath: item.docPath || prev.docPath,
      researched: Boolean(prev.researched || item.researched),
      title: item.title || prev.title,
      subtitle: item.subtitle || prev.subtitle,
      body: item.body || prev.body,
      value: item.value ?? prev.value,
    });
  }
  return [...merged.values()];
}

function docsFromSections(value: unknown): { path?: string; title?: string; kind?: string; present?: boolean }[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    path: textOf(item.path),
    title: textOf(item.title, undefined),
    kind: textOf(item.kind, undefined),
    present: typeof item.present === "boolean" ? item.present : undefined,
  })).filter((item) => item.path || item.kind);
}

function knownDoc(docs: { path?: string; title?: string; kind?: string; present?: boolean }[], path: string, kinds: string[]): MeetingDocLink {
  const match = docs.find((doc) => doc.path === path || (doc.kind ? kinds.includes(doc.kind.toLowerCase()) : false));
  return {
    path: match?.path || path,
    title: match?.title,
    present: Boolean(match && match.present !== false),
  };
}

function useLiveMeetingState(meetingId?: string): MeetingState {
  const contextMeetingId = useContext(MeetingScopeContext);
  const scopedMeetingId = meetingId ?? contextMeetingId;
  const meetings = safeArray(useLiveMeetings());
  const selected = useMemo(
    () => scopedMeetingId ? resolveMeeting(meetings, scopedMeetingId) : pickMeeting(meetings),
    [meetings, scopedMeetingId],
  );
  const live = useMeetingLive(selected.id, selected.session_uid ?? "");
  const actions = useCanvasActionState();
  const [recorded, setRecorded] = useState<TranscriptLine[]>([]);

  useEffect(() => {
    if (selected.session_uid || !selected.native_id) {
      setRecorded([]);
      return;
    }
    let cancelled = false;
    void fetchTranscript(selected.platform, selected.native_id).then((lines) => {
      if (!cancelled) setRecorded(lines);
    });
    return () => { cancelled = true; };
  }, [selected.id, selected.native_id, selected.platform, selected.session_uid]);

  return useMemo(() => {
    const participants = safeArray(selected.participants);
    const normalizedSelected = {
      ...selected,
      participants,
      mentioned: safeArray(selected.mentioned),
      actions: safeArray(selected.actions),
      transcript: safeArray(selected.transcript),
      insights: safeArray(selected.insights),
      docs: safeArray(selected.docs),
    };
    const liveSegments = safeArray(live.transcript).map((s) => ({ id: s.id, speaker: s.speaker, text: cleanTranscriptText(s.text), ts: s.t }));
    const recordedSegments = safeArray(recorded).map((s) => ({ speaker: s.speaker, text: cleanTranscriptText(s.text), ts: lineTs(s) }));
    const fallbackSegments = normalizedSelected.transcript.map((s) => ({ speaker: s.speaker, text: cleanTranscriptText(s.text), ts: lineTs(s) }));
    const segments = selected.session_uid ? liveSegments : (recordedSegments.length ? recordedSegments : fallbackSegments);
    const copilotCards = safeArray(live.cards).map((c, i) => ({ id: `live-${i}-${c.kind}-${c.title}`, kind: c.kind, title: cleanTranscriptText(c.title), body: c.body ? cleanTranscriptText(c.body) : c.body }));
    // The copilot surfaces ENTITY mentions (people/companies/products/numbers) as cards too. Route
    // those into the entity groups (they dedup downstream via mergeEntityItems) and keep only real
    // SIGNAL cards as `cards`, so a person mentioned 4 times doesn't pile up 4× in the Signals column.
    const isEntityCard = (k: unknown) => /person|people|compan|product|number|num/i.test(textOf(k));
    const cards: MeetingState["cards"] = [
      ...copilotCards.filter((c) => !isEntityCard(c.kind)),
      ...normalizedSelected.insights.map((c, i) => ({ id: `insight-${i}`, kind: cardKindFromText(c.text), title: c.text, ts: c.t })),
      ...normalizedSelected.actions.map((a) => ({ id: a.id, kind: "action", title: a.label, body: a.detail })),
    ];
    const { present, detected } = meetingEntities(normalizedSelected);
    const people = [
      ...present,
      ...participants.map((p) => ({ title: p.name, role: p.role, initials: p.initials })),
      ...copilotCards.filter((c) => /person|people/i.test(textOf(c.kind))).map((c) => ({ title: c.title, summary: c.body })),
    ];
    const companies = [
      ...detected.filter((e) => e.type === "company"),
      ...copilotCards.filter((c) => /compan/i.test(textOf(c.kind))).map((c) => ({ title: c.title, summary: c.body })),
    ];
    const products = [
      ...detected.filter((e) => e.type === "topic" || e.type === "task"),
      ...copilotCards.filter((c) => /product/i.test(textOf(c.kind))).map((c) => ({ title: c.title, summary: c.body })),
    ];
    const textCorpus = [...segments.map((s) => s.text), ...copilotCards.flatMap((c) => [c.title, c.body ?? ""])];
    const numbers = extractNotableNumbers(textCorpus);
    return {
      meeting: {
        id: selected.id,
        nativeId: selected.native_id,
        title: selected.title,
        status: selected.live_status ?? selected.status,
        startedAt: selected.scheduled_at,
        participants: participants.map((p) => p.name),
        docs: normalizedSelected.docs.map((doc) => ({
          path: doc.path,
          title: doc.title,
          kind: doc.kind,
          present: true,
        })),
      },
      transcript: {
        segments,
        liveCaption: selected.session_uid ? latestCaption(live.transcript, live.note) : undefined,
        notes: safeArray(live.notes).map((note) => ({
          id: note.id,
          speaker: note.speaker,
          chapter: note.chapter,
          text: cleanTranscriptText(note.text),
          ts: note.t,
          pass: note.pass,
          frozen: note.frozen,
        })),
      },
      entities: { people, companies, products, numbers },
      cards,
      metrics: {
        participants: participants.length,
        cards: cards.length,
        transcriptSegments: segments.length,
        ...actions.metrics,
      },
      sections: actions.sections,
    };
  }, [actions.metrics, actions.sections, live.cards, live.note, live.notes, live.transcript, recorded, selected]);
}

function playbackIntervalMs(speed: number): number {
  return Math.max(350, Math.round(1800 / Math.max(0.5, speed)));
}

export function MeetingSourceProvider({ meetingId, children }: { meetingId?: string; children: ReactNode }) {
  const contextMeetingId = useContext(MeetingScopeContext);
  const scopedMeetingId = meetingId ?? contextMeetingId;
  const live = useLiveMeetingState(scopedMeetingId);
  const [mode, setMode] = useState<MeetingSourceMode>("live");
  const [mockState, setMockState] = useState<MockSourceState>(() => createMockSourceState());

  useEffect(() => {
    if (!mockState.playing || typeof window === "undefined") return;
    const id = window.setInterval(() => {
      setMockState((current) => stepMockSource(current));
    }, playbackIntervalMs(mockState.speed));
    return () => window.clearInterval(id);
  }, [mockState.playing, mockState.speed]);

  const mock = useMemo(() => buildMockMeetingState(mockState), [mockState]);
  const liveHasData = meetingStateHasData(live);
  // Only auto-fall-back to mock when NOTHING real is bound (the standalone canvas preview). A real
  // bound meeting (the meeting page always passes a meetingId) shows its LIVE state even when sparse
  // — the real empty states, never the mock scenario — unless the user explicitly toggles Mock.
  const boundToRealMeeting = Boolean(scopedMeetingId);
  const activeMode: MeetingSourceMode = mode === "mock" ? "mock" : (boundToRealMeeting || liveHasData ? "live" : "mock");
  const state = activeMode === "mock" ? mock : live;

  const play = useCallback(() => setMockState((current) => setMockPlaying(current, true)), []);
  const pause = useCallback(() => setMockState((current) => setMockPlaying(current, false)), []);
  const step = useCallback(() => setMockState((current) => stepMockSource(setMockPlaying(current, false))), []);
  const reset = useCallback(() => setMockState((current) => resetMockSource(current)), []);
  const selectScenario = useCallback((scenarioId: MockScenarioId) => setMockState((current) => setMockScenario(current, scenarioId)), []);
  const selectSpeed = useCallback((speed: number) => setMockState((current) => setMockSpeed(current, speed)), []);
  const inject = useCallback((kind: MockInjectKind) => setMockState((current) => injectMockItem(current, kind)), []);
  const controls = useMemo<MeetingSourceControls>(() => ({
    play,
    pause,
    step,
    reset,
    setScenario: selectScenario,
    setSpeed: selectSpeed,
    inject,
  }), [inject, pause, play, reset, selectScenario, selectSpeed, step]);

  const scenarios = useMemo(() => MOCK_SCENARIOS.map((scenario) => ({ id: scenario.id, label: scenario.label })), []);
  const value = useMemo<MeetingSourceContextValue>(() => ({
    state,
    live,
    mock,
    mode,
    activeMode,
    liveHasData,
    scenarios,
    mockState,
    setMode,
    controls,
  }), [activeMode, controls, live, liveHasData, mock, mockState, mode, scenarios, state]);

  return createElement(MeetingSourceContext.Provider, { value }, children);
}

export function useMeetingSource(): MeetingSourceContextValue | null {
  return useContext(MeetingSourceContext);
}

export function useMeeting(_meetingId?: string): MeetingState {
  return useContext(MeetingSourceContext)?.state ?? DEFAULT_MEETING_STATE;
}

export function useTranscript(opts?: { by?: "time" | "speaker"; window?: number }): { segments: TranscriptSegment[]; liveCaption?: string } {
  const meeting = useMeeting();
  return useMemo(() => {
    const source = normalizeSegments(meeting.transcript.segments);
    const limit = Number.isFinite(opts?.window) ? Math.max(0, Math.floor(opts?.window ?? 0)) : 0;
    const windowed = limit > 0 ? source.slice(-limit) : source;
    const segments = opts?.by === "speaker"
      ? windowed
        .map((segment, index) => ({ segment, index }))
        .sort((a, b) => textOf(a.segment.speaker, "Speaker").localeCompare(textOf(b.segment.speaker, "Speaker")) || a.index - b.index)
        .map((entry) => entry.segment)
      : windowed;
    return { segments, liveCaption: textOf(meeting.transcript.liveCaption, undefined) };
  }, [meeting.transcript.liveCaption, meeting.transcript.segments, opts?.by, opts?.window]);
}

export function useSpeakers(): SpeakerSummary[] {
  const { segments } = useTranscript({ by: "time" });
  return useMemo(() => {
    const totals = new Map<string, { name: string; segments: number; talkMs: number }>();
    segments.forEach((segment, index) => {
      const name = textOf(segment.speaker, "Speaker") || "Speaker";
      const current = totals.get(name) ?? { name, segments: 0, talkMs: 0 };
      const start = parseTimestampMs(segment.ts);
      const next = parseTimestampMs(segments[index + 1]?.ts);
      const measured = start != null && next != null && next > start ? Math.min(next - start, 120_000) : undefined;
      current.segments += 1;
      current.talkMs += measured ?? estimateTalkMs(segment.text);
      totals.set(name, current);
    });
    const totalMs = [...totals.values()].reduce((sum, speaker) => sum + speaker.talkMs, 0);
    return [...totals.values()]
      .map((speaker) => ({ ...speaker, talkPct: totalMs > 0 ? Math.round((speaker.talkMs / totalMs) * 100) : 0 }))
      .sort((a, b) => b.talkMs - a.talkMs || a.name.localeCompare(b.name));
  }, [segments]);
}

export function useEntities(opts?: { kind?: EntityKind }): EntityItem[] {
  const meeting = useMeeting();
  const { segments } = useTranscript({ by: "time" });
  return useMemo(() => {
    const all = mergeEntityItems([
      ...safeArray(meeting.entities.people).map((item, index) => enrichEntity("person", item, index, segments)),
      ...safeArray(meeting.entities.companies).map((item, index) => enrichEntity("company", item, index, segments)),
      ...safeArray(meeting.entities.products).map((item, index) => enrichEntity("product", item, index, segments)),
      ...safeArray(meeting.entities.numbers).map((item, index) => enrichEntity("number", item, index, segments)),
    ]);
    return opts?.kind ? all.filter((entity) => entity.kind === opts.kind) : all;
  }, [meeting.entities.companies, meeting.entities.numbers, meeting.entities.people, meeting.entities.products, opts?.kind, segments]);
}

export function useSignals(): EntityItem[] {
  const meeting = useMeeting();
  return useMemo(() => {
    const seen = new Map<string, EntityItem>();
    safeArray(meeting.cards).forEach((card, index) => {
      const title = textOf(card.title, `Signal ${index + 1}`);
      const key = slug(title);
      if (seen.has(key)) return;
      const body = textOf(card.body);
      seen.set(key, {
        id: card.id || `signal:${index}`,
        kind: "signal" as const,
        name: title,
        context: oneWord(card.kind, "Signal"),
        summary: body || title,
        quote: body || undefined,
        researched: false,
        title,
        body,
      });
    });
    return [...seen.values()];
  }, [meeting.cards]);
}

export function useMeetingDocs(): { brief: MeetingDocLink; report: MeetingDocLink } {
  const meeting = useMeeting();
  return useMemo(() => {
    const key = slug(meeting.meeting.nativeId || meeting.meeting.id || meeting.meeting.title || "meeting");
    const briefPath = `kg/entities/meeting/${key}.md`;
    const reportPath = `kg/entities/meeting/${key}-report.md`;
    const docs = [
      ...safeArray(meeting.meeting.docs),
      ...docsFromSections(meeting.sections.docs),
    ];
    return {
      brief: knownDoc(docs, briefPath, ["brief", "prep", "meeting"]),
      report: knownDoc(docs, reportPath, ["report", "post-meeting-report"]),
    };
  }, [meeting.meeting.docs, meeting.meeting.id, meeting.meeting.nativeId, meeting.meeting.title, meeting.sections.docs]);
}
