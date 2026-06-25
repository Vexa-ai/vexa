"use client";
import { useEffect, useMemo, useState } from "react";
import { useLiveMeetings, fetchTranscript } from "../surfaces/liveMeetings";
import { meetingEntities, type MeetingMock, type TranscriptLine } from "../surfaces/mock";
import { useMeetingLive } from "../surfaces/meetingLive";
import { useCanvasActionState } from "./actions";
import type { MeetingState } from "./types";

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

function pickMeeting(meetings: MeetingMock[]): MeetingMock {
  return meetings.find((m) => m.status === "live") ?? meetings[0] ?? EMPTY_MEETING;
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

export function useMeeting(): MeetingState {
  const meetings = useLiveMeetings();
  const selected = pickMeeting(meetings);
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
    const liveSegments = live.transcript.map((s) => ({ speaker: s.speaker, text: s.text, ts: s.t }));
    const recordedSegments = recorded.map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const fallbackSegments = selected.transcript.map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const segments = selected.session_uid ? liveSegments : (recordedSegments.length ? recordedSegments : fallbackSegments);
    const cards: MeetingState["cards"] = [
      ...live.cards.map((c, i) => ({ id: `live-${i}-${c.kind}-${c.title}`, kind: c.kind, title: c.title, body: c.body })),
      ...selected.insights.map((c, i) => ({ id: `insight-${i}`, kind: "insight", title: c.text, ts: c.t })),
      ...selected.actions.map((a) => ({ id: a.id, kind: "action", title: a.label, body: a.detail })),
    ];
    const { present, detected } = meetingEntities(selected);
    const people = [
      ...present,
      ...selected.participants.map((p) => ({ title: p.name, role: p.role, initials: p.initials })),
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
        participants: selected.participants.map((p) => p.name),
      },
      transcript: {
        segments,
        liveCaption: selected.session_uid ? latestCaption(live.transcript, live.note) : undefined,
      },
      entities: { people, companies, products, numbers },
      cards,
      metrics: {
        participants: selected.participants.length,
        cards: cards.length,
        transcriptSegments: segments.length,
        ...actions.metrics,
      },
      sections: actions.sections,
    };
  }, [actions.metrics, actions.sections, live.cards, live.note, live.transcript, recorded, selected]);
}
