"use client";
import { createContext, createElement, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLiveMeetings, fetchDurableTranscript, type DurableTranscript } from "../surfaces/liveMeetings";
import { meetingEntities, type MeetingMock, type TranscriptLine } from "../surfaces/meetingModel";
import { useMeetingLive } from "../surfaces/meetingLive";
import { useCanvasActionState } from "./actions";
import { cleanTranscriptText, extractNotableNumbers } from "./textSignals";
import type { EntityItem, EntityKind, MeetingDocLink, MeetingState, SpeakerSummary, TranscriptSegment } from "./types";

const EMPTY_MEETING: MeetingMock = {
  id: "live",
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

interface MeetingSourceContextValue {
  state: MeetingState;
}

const MeetingSourceContext = createContext<MeetingSourceContextValue | null>(null);
const EMPTY_MEETING_STATE: MeetingState = {
  meeting: { id: "", title: "" },
  transcript: { segments: [] },
  entities: { people: [], companies: [], products: [], numbers: [] },
  cards: [],
  diagnostics: {},
  metrics: {},
  sections: {},
};

function pickMeeting(meetings: MeetingMock[]): MeetingMock {
  return meetings.find((m) => m.status === "live") ?? meetings[0] ?? EMPTY_MEETING;
}

function matchesMeeting(m: MeetingMock, meetingId: string): boolean {
  return m.id === meetingId || m.native_id === meetingId;
}

function unresolvedMeeting(meetingId: string): MeetingMock {
  return { ...EMPTY_MEETING, id: meetingId, title: "Meeting" };
}

/** F169 — "the panel stays on the ended meeting". A bot can be RE-DROPPED into the SAME call (the
 *  person says "send bot" again after the first session ended, or an auto-reconnect fires):
 *  meeting-api mints a NEW row (a new `id`) for the new session, but the underlying call's
 *  `native_id` (the Meet/Zoom code) is unchanged. A tab pinned to the OLD row's id used to keep
 *  rendering that row's now-static transcript forever — `resolveMeeting` only ever looked up the
 *  exact id it was given, so the pane never learned a fresher session for the same call existed.
 *
 *  Exported (pure, no I/O) so this is provable without mounting the hook: given the resolved row
 *  and the full list, is there a LIVE row for the same call, newer than this one? Row ids are
 *  sequential ints assigned in creation order (matchesMeeting/toMock's own comment), so the
 *  highest numeric id among same-`native_id` live rows is unambiguously the newest session —
 *  robust even when a fresh row's `start_time` hasn't landed yet (still `requested`/`joining`). */
export function newerActiveSameCall(meetings: MeetingMock[], resolved: MeetingMock): MeetingMock | undefined {
  if (resolved.status === "live" || !resolved.native_id) return undefined;
  const candidates = meetings.filter(
    (m) => m.status === "live" && m.native_id === resolved.native_id && m.id !== resolved.id,
  );
  if (!candidates.length) return undefined;
  return candidates.reduce((newest, m) => (Number(m.id) > Number(newest.id) ? m : newest));
}

function resolveMeeting(meetings: MeetingMock[], meetingId: string): MeetingMock {
  // A real meeting id must NEVER resolve to a MOCK meeting. If it isn't in the live+past list yet,
  // show an empty placeholder for THAT id (it fills in when the list loads) — not a wrong meeting.
  const resolved = meetings.find((m) => matchesMeeting(m, meetingId)) ?? unresolvedMeeting(meetingId);
  // FOLLOW the newer active session for the same call rather than freezing on a row that already
  // ended (F169). A direct open of an id with no newer active session for its call is unaffected —
  // it resolves to itself exactly as before, and terminal status renders "ended" normally.
  return newerActiveSameCall(meetings, resolved) ?? resolved;
}

function latestCaption(segments: { text: string; completed?: boolean }[]): string | undefined {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim() && seg.completed === false) return seg.text;
  }
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.text.trim()) return seg.text;
  }
  return undefined;
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
      id: segment.id,
      speaker: textOf(segment.speaker, "Speaker"),
      text: textOf(segment.text),
      ts: segment.ts,
      tsMs: segment.tsMs,
      completed: segment.completed,
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
    if (haystack.includes(needle) || haystack.replace(/\s+/g, "").includes(compactNeedle)) return text;
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

/** ADR-0027 / P21 — the live-subscription uid, PINNED across the row's terminal flip. The list
 *  row's live flag is INTENT (it clears the moment the FSM stops); the stream's own `meeting-end`
 *  (sent only after the worker's view_end marker) is the EVIDENCE the subscription releases on.
 *  Without the pin, the stop transition changed the subscription key, which silently swapped the
 *  hook onto a fresh empty store — every live note vanished from the pane at the exact moment the
 *  final beat was still arriving, and a one-shot durable fetch raced the marker drain: the
 *  "processed notes disappear on stop" defect every earlier view-layer patch worked around.
 *  Single-slot memory: switching to another meeting drops the pin (a completed meeting re-opened
 *  later hydrates from the durable row, which the marker protocol guarantees complete). */
export function pinSubscriptionUid(
  mem: { id: string; uid: string }, id: string, sessionUid: string | undefined,
): { id: string; uid: string } {
  if (sessionUid) return { id, uid: sessionUid };
  return mem.id === id ? mem : { id: "", uid: "" };
}

function useLiveMeetingState(meetingId?: string): MeetingState {
  const contextMeetingId = useContext(MeetingScopeContext);
  const scopedMeetingId = meetingId ?? contextMeetingId;
  const meetings = safeArray(useLiveMeetings());
  const selected = useMemo(
    () => scopedMeetingId ? resolveMeeting(meetings, scopedMeetingId) : pickMeeting(meetings),
    [meetings, scopedMeetingId],
  );
  // Subscribe by the PINNED uid — the connection outlives the row's terminal flip and ends when
  // the SERVER ends it (`meeting-end` → the store self-closes; state survives in the module map).
  const pinRef = useRef({ id: "", uid: "" });
  pinRef.current = pinSubscriptionUid(pinRef.current, selected.id, selected.session_uid);
  const live = useMeetingLive(selected.id, pinRef.current.uid);
  const actions = useCanvasActionState();
  const [durable, setDurable] = useState<DurableTranscript>({ lines: [] });

  // Hydrate the RECORDED segments from the durable store. For a past meeting this is the only
  // source; for a live one the live stream wins and this is the seed. (There is no second,
  // "processed" body to wait for any more — PRD decision 34 removed the producer, so the
  // bounded catch-up retry that existed to wait out the copilot's final beat went with it.)
  const effStatus = selected.live_status ?? selected.status;
  useEffect(() => {
    setDurable({ lines: [] });
    // P0 (wrong-row hydration fix): hydrate by the meetings-domain ROW id (`selected.id`), so the pane
    // shows EXACTLY this row's durable segments + processed notes — never the newest row sharing the
    // native (the old native-keyed fetch). `native_id` presence still gates a real (resolved) meeting vs
    // the unresolved placeholder, but the fetch key is the row id.
    if (!selected.native_id || !selected.id) return;
    const rowId = selected.id;
    const terminal = effStatus === "completed" || effStatus === "failed" || effStatus === "stopped";
    let cancelled = false;
    void fetchDurableTranscript(rowId).then((next) => {
      if (!cancelled) setDurable(next);
    });
    return () => { cancelled = true; };
  }, [selected.id, selected.native_id, selected.platform, selected.session_uid, effStatus, live.ended]);

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
    // THE TRANSCRIPT IS RENDERED AS IT WAS SAID. These three mappers — live, durable and
    // fallback — each ran every line through `cleanTranscriptText`, whose `DOMAIN_CORRECTIONS`
    // table silently rewrote words: `Entropic`->`Anthropic`, `Cloud Code`->`Claude Code`,
    // `Yalna Kunz`->`Yann LeCun`. That is the deleted in-product inference pipeline still
    // editing the record (decision 34 removed it; decision 12 makes the transcript the live
    // canvas), with no indication, in the artefact a pilot treats as what was said.
    //
    // It was also breaking decision 35 mechanically: `splitTextIntoSpans` matches published
    // terms against the text it is drawing, so a term the agent extracted from the RAW
    // transcript could never match its own chip in the REWRITTEN one.
    const liveSegments = safeArray(live.transcript).map((s) => ({ id: s.id, speaker: s.speaker, text: s.text, ts: s.t, tsMs: s.tsMs, completed: s.completed }));
    const recordedSegments = safeArray(durable.lines).map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const fallbackSegments = normalizedSelected.transcript.map((s) => ({ speaker: s.speaker, text: s.text, ts: lineTs(s) }));
    const segments = selected.session_uid ? liveSegments : (recordedSegments.length ? recordedSegments : fallbackSegments);
    const diagnostics = {
      liveConnected: live.connected,
      ended: live.ended,
      reconnects: live.reconnects,
      lastEventAt: live.lastEventAt,
      lastTranscriptAt: live.lastTranscriptAt,
      issues: safeArray(live.issues).map((issue) => ({
        kind: issue.kind,
        message: cleanTranscriptText(issue.message),
        status: issue.status,
        at: issue.at,
      })),
    };
    // Cards come from the MEETING RECORD only (its insights and proposed actions). The live
    // copilot that used to push cards onto this feed is gone (PRD decision 34).
    const cards: MeetingState["cards"] = [
      ...normalizedSelected.insights.map((c, i) => ({ id: `insight-${i}`, kind: cardKindFromText(c.text), title: c.text, ts: c.t })),
      ...normalizedSelected.actions.map((a) => ({ id: a.id, kind: "action", title: a.label, body: a.detail })),
    ];
    const { present, detected } = meetingEntities(normalizedSelected);
    const people = [
      ...present,
      ...participants.map((p) => ({ title: p.name, role: p.role, initials: p.initials })),
    ];
    const companies = detected.filter((e) => e.type === "company");
    const products: unknown[] = [];
    const numbers = extractNotableNumbers(segments.map((s) => s.text));
    return {
      meeting: {
        id: selected.id,
        nativeId: selected.native_id,
        title: selected.title,
        status: selected.live_status ?? selected.status,
        live: Boolean(selected.session_uid),
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
        liveCaption: selected.session_uid ? latestCaption(live.transcript) : undefined,
      },
      entities: { people, companies, products, numbers },
      cards,
      diagnostics,
      metrics: {
        participants: participants.length,
        cards: cards.length,
        transcriptSegments: segments.length,
        ...actions.metrics,
      },
      sections: actions.sections,
    };
  }, [
    actions.metrics,
    actions.sections,
    live.connected,
    live.ended,
    live.issues,
    live.lastEventAt,
    live.lastTranscriptAt,
    live.reconnects,
    live.transcript,
    durable,
    selected,
  ]);
}

export function MeetingSourceProvider({ meetingId, children }: { meetingId?: string; children: ReactNode }) {
  const contextMeetingId = useContext(MeetingScopeContext);
  const scopedMeetingId = meetingId ?? contextMeetingId;
  const live = useLiveMeetingState(scopedMeetingId);
  const value = useMemo<MeetingSourceContextValue>(() => ({ state: live }), [live]);
  return createElement(MeetingSourceContext.Provider, { value }, children);
}

export function useMeetingSource(): MeetingSourceContextValue | null {
  return useContext(MeetingSourceContext);
}

export function useMeeting(_meetingId?: string): MeetingState {
  return useContext(MeetingSourceContext)?.state ?? EMPTY_MEETING_STATE;
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
