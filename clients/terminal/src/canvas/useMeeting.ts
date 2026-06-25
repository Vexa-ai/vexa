"use client";
import { createContext, createElement, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLiveMeetings, fetchTranscript } from "../surfaces/liveMeetings";
import { meetingById, meetingEntities, type MeetingMock, type TranscriptLine } from "../surfaces/mock";
import { useMeetingLive } from "../surfaces/meetingLive";
import { useCanvasActionState } from "./actions";
import type { CanvasEntity, EntityKind, MeetingState, SpeakerSummary, TranscriptSegment } from "./types";

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
  return meetings.find((m) => matchesMeeting(m, meetingId)) ?? meetingById(meetingId) ?? unresolvedMeeting(meetingId);
}

function latestCaption(segments: { text: string; completed?: boolean }[], note: string): string | undefined {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim() && seg.completed === false) return seg.text;
  }
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim()) return seg.text;
  }
  return note.trim() || undefined;
}

function extractNumbers(texts: string[]): { text: string; value?: number }[] {
  const out: { text: string; value?: number }[] = [];
  const re = /(?:[$€£]\s*)?\b\d[\d,]*(?:\.\d+)?%?\b/g;
  for (const text of texts) {
    const matches = text.match(re) ?? [];
    for (const raw of matches) {
      const cleaned = raw.replace(/[^0-9.-]/g, "");
      const value = cleaned ? Number(cleaned) : undefined;
      out.push({ text: raw, value: Number.isFinite(value) ? value : undefined });
    }
  }
  return out.slice(-24);
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
      text: textOf(segment.text),
      ts: segment.ts,
    }))
    .filter((segment) => segment.text.trim());
}

function entityTitle(item: unknown, fallback: string): string {
  if (typeof item === "string") return item;
  return textOf(field(item, "title") ?? field(item, "name") ?? field(item, "text") ?? field(item, "value"), fallback);
}

function normalizeEntity(kind: EntityKind, item: unknown, index: number): CanvasEntity {
  return {
    kind,
    title: entityTitle(item, `${kind} ${index + 1}`),
    subtitle: textOf(field(item, "subtitle") ?? field(item, "role"), undefined),
    body: textOf(field(item, "summary") ?? field(item, "body"), undefined),
    value: numberOf(field(item, "value")) ?? textOf(field(item, "value"), undefined),
  };
}

export function useMeeting(meetingId?: string): MeetingState {
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
    };
    const liveSegments = safeArray(live.transcript).map((s) => ({ speaker: s.speaker, text: s.text, ts: s.t }));
    const recordedSegments = safeArray(recorded).map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const fallbackSegments = normalizedSelected.transcript.map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const segments = selected.session_uid ? liveSegments : (recordedSegments.length ? recordedSegments : fallbackSegments);
    const cards: MeetingState["cards"] = [
      ...safeArray(live.cards).map((c, i) => ({ id: `live-${i}-${c.kind}-${c.title}`, kind: c.kind, title: c.title, body: c.body })),
      ...normalizedSelected.insights.map((c, i) => ({ id: `insight-${i}`, kind: "insight", title: c.text, ts: c.t })),
      ...normalizedSelected.actions.map((a) => ({ id: a.id, kind: "action", title: a.label, body: a.detail })),
    ];
    const { present, detected } = meetingEntities(normalizedSelected);
    const people = [
      ...present,
      ...participants.map((p) => ({ title: p.name, role: p.role, initials: p.initials })),
    ];
    const companies = detected.filter((e) => e.type === "company");
    const products = detected.filter((e) => e.type === "topic" || e.type === "task");
    const textCorpus = [...segments.map((s) => s.text), ...cards.flatMap((c) => [c.title, c.body ?? ""])];
    const numbers = extractNumbers(textCorpus);
    return {
      meeting: {
        id: selected.id,
        title: selected.title,
        startedAt: selected.scheduled_at,
        participants: participants.map((p) => p.name),
      },
      transcript: {
        segments,
        liveCaption: selected.session_uid ? latestCaption(live.transcript, live.note) : undefined,
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
  }, [actions.metrics, actions.sections, live.cards, live.note, live.transcript, recorded, selected]);
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

export function useEntities(opts?: { kind?: EntityKind }): CanvasEntity[] {
  const meeting = useMeeting();
  return useMemo(() => {
    const all: CanvasEntity[] = [
      ...safeArray(meeting.entities.people).map((item, index) => normalizeEntity("person", item, index)),
      ...safeArray(meeting.entities.companies).map((item, index) => normalizeEntity("company", item, index)),
      ...safeArray(meeting.entities.products).map((item, index) => normalizeEntity("product", item, index)),
      ...safeArray(meeting.entities.numbers).map((item, index) => normalizeEntity("number", item, index)),
    ];
    return opts?.kind ? all.filter((entity) => entity.kind === opts.kind) : all;
  }, [meeting.entities.companies, meeting.entities.numbers, meeting.entities.people, meeting.entities.products, opts?.kind]);
}
