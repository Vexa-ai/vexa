"use client";
/** Live meeting notes condense the running transcript into attributed, first-person paragraphs. */
import { useMemo } from "react";
import { useEntities, useMeeting, useTranscript } from "./useMeeting";
import { cleanProcessedNoteText, cleanTranscriptText } from "./textSignals";
import type { EntityItem, ProcessedTranscriptNote, TranscriptSegment } from "./types";

export type NoteTagKind = "company" | "person" | "product";
export interface NoteTag { id: string; label: string; kind: NoteTagKind; context?: string; at?: number; end?: number }
export interface MeetingNote {
  id: string;
  ts?: string;             // pre-formatted meeting-relative label — back-compat
  tsMs?: number;           // ABSOLUTE wall-clock time of the line, epoch ms (UTC). Renderer formats in local TZ.
  completed?: boolean;     // false = live pending (in-progress ASR); true/undefined = finalized
  speaker?: string;
  chapter?: string;
  text: string;
  tags: NoteTag[];
}

const CLUSTER = 3;        // utterances folded into one note
const MAX_NOTE_CHARS = 240;
const MAX_TAGS = 6;

// Capitalized words we never tag as entities — they're almost always just sentence-start casing or filler.
const STOPWORDS = new Set([
  "i", "a", "an", "the", "this", "that", "these", "those", "our", "my", "your", "his", "her", "their", "its",
  "we", "you", "he", "she", "they", "it", "so", "and", "but", "or", "now", "then", "great", "yeah", "yes",
  "no", "not", "well", "ok", "okay", "how", "what", "when", "where", "why", "who", "everyone", "everything",
  "definitions", "has", "is", "are", "was", "were", "do", "does", "did", "let", "look", "mean", "actually",
  "maybe", "sure", "right", "good", "of", "in", "on", "at", "to", "for", "if", "as", "there", "here", "yeah",
  "speaker",
  "european", "american", "chinese", "british", "french", "german", "indian", "japanese", "russian", "global",
  "western", "eastern", "northern", "southern",
]);

function formatTs(ts: number | string | undefined): string {
  if (typeof ts === "number" && Number.isFinite(ts)) {
    const total = Math.max(0, Math.floor(ts));
    const m = Math.floor(total / 60), s = total % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  const raw = typeof ts === "string" ? ts.trim() : "";
  const m = raw.match(/\d{1,2}:\d{2}(?::\d{2})?/);
  return m ? m[0] : "";
}

function speakerOf(segment: Pick<TranscriptSegment, "speaker"> | undefined): string {
  const speaker = (segment?.speaker ?? "").trim();
  return speaker || "Speaker";
}

/** Absolute wall-clock (epoch ms) for a note: take the last segment in the group that carries one,
 *  so the note is timestamped at when its final utterance was spoken. */
function groupTsMs(group: { tsMs?: number }[]): number | undefined {
  for (let i = group.length - 1; i >= 0; i--) {
    const ms = group[i]?.tsMs;
    if (typeof ms === "number" && Number.isFinite(ms)) return ms;
  }
  return undefined;
}

/** A group is pending (live, in-progress) if its last segment is explicitly not completed. */
function groupPending(group: { completed?: boolean }[]): boolean {
  return group.length > 0 && group[group.length - 1]?.completed === false;
}

/** Shorten a cluster of utterances to ~one or two sentences without paraphrasing — keep the speaker's
 *  voice, just trim. Collapses whitespace and stops at a sentence boundary under the char budget. */
function condense(text: string): string {
  const clean = cleanTranscriptText(text);
  if (clean.length <= MAX_NOTE_CHARS) return clean;
  const sentences = clean.match(/[^.!?]+[.!?]+|\S[^.!?]*$/g) ?? [clean];
  let out = "";
  for (const s of sentences) {
    if ((out + s).length > MAX_NOTE_CHARS) break;
    out += s;
  }
  out = out.trim();
  return out || `${clean.slice(0, MAX_NOTE_CHARS).trim()}…`;
}

function pushTag(acc: NoteTag[], seen: Set<string>, tag: Omit<NoteTag, "id">, index: number): void {
  const key = tag.label.toLowerCase();
  if (seen.has(key) || acc.length >= MAX_TAGS) return;
  seen.add(key);
  acc.push({ ...tag, id: `tag-${index}-${key.replace(/[^a-z0-9]+/g, "-")}` });
}

function overlaps(found: { at: number; end: number }[], at: number, end: number): boolean {
  return found.some((item) => at < item.end && end > item.at);
}

function titleCaseStart(text: string): string {
  return text.replace(/^[a-z]/, (ch) => ch.toUpperCase());
}

function tagKind(kind: string | undefined): NoteTagKind | undefined {
  const normalized = (kind ?? "").toLowerCase();
  if (normalized === "person" || normalized === "company" || normalized === "product") return normalized;
  return undefined;
}

function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findEntitySpan(text: string, label: string): { at: number; end: number } | undefined {
  const pattern = new RegExp(`(^|[^\\p{L}\\p{N}_])(${escapeRe(label)})(?=$|[^\\p{L}\\p{N}_])`, "iu");
  const match = pattern.exec(text);
  if (!match || match.index == null) return undefined;
  const at = match.index + match[1].length;
  return { at, end: at + match[2].length };
}

/** Render tags only for explicit meeting entities. The model/entity pass owns what is tag-worthy. */
export function extractNoteTags(text: string, entities: EntityItem[], index: number): NoteTag[] {
  const found: { at: number; end: number; tag: Omit<NoteTag, "id"> }[] = [];
  const seenCandidates = new Set<string>();
  for (const e of entities) {
    const kind = tagKind(e.kind);
    if (!kind) continue;
    const label = cleanTranscriptText(e.name ?? e.title ?? "").replace(/['']s$/i, "").trim();
    const key = `${kind}:${label.toLowerCase()}`;
    if (!label || label.length < 2 || STOPWORDS.has(label.toLowerCase()) || seenCandidates.has(key)) continue;
    if (kind === "person" && !/\s/.test(label)) continue;
    seenCandidates.add(key);
    const span = findEntitySpan(text, label);
    if (!span || overlaps(found, span.at, span.end)) continue;
    if (!/[A-Z0-9]/.test(text.slice(span.at, span.end))) continue;
    found.push({
      at: span.at,
      end: span.end,
      tag: {
        label,
        kind,
        context: e.context,
        at: span.at,
        end: span.end,
      },
    });
  }

  found.sort((a, b) => a.at - b.at);
  const acc: NoteTag[] = [];
  const seen = new Set<string>();
  for (const f of found) pushTag(acc, seen, f.tag, index);
  return acc;
}

function processedMeetingNote(note: ProcessedTranscriptNote, entities: EntityItem[], index: number): MeetingNote | null {
  const text = titleCaseStart(condense(cleanProcessedNoteText(note.text)));
  if (!text) return null;
  return {
    id: note.id || `processed-${index}`,
    ts: formatTs(note.ts),
    tsMs: typeof note.tsMs === "number" && Number.isFinite(note.tsMs) ? note.tsMs : undefined,
    completed: note.completed,
    speaker: speakerOf(note),
    chapter: cleanTranscriptText(note.chapter ?? ""),
    text,
    tags: extractNoteTags(text, entities, index),
  };
}

function fallbackMeetingNotes(segments: TranscriptSegment[], entities: EntityItem[], skipIds: Set<string>): MeetingNote[] {
  const segs = segments.filter((s) => (s.text ?? "").trim() && !(s.id && skipIds.has(s.id)));
  const notes: MeetingNote[] = [];
  let group: TranscriptSegment[] = [];
  let groupStart = 0;
  let groupSpeaker = "";

  const pushGroup = (force: boolean) => {
    // Always emit a PENDING (live, in-progress) tail so the in-flight line reaches the view — even
    // when the group is shorter than CLUSTER and we wouldn't normally flush it yet.
    if (!group.length || (!force && !groupPending(group) && group.length < CLUSTER)) return;
    const text = titleCaseStart(condense(group.map((s) => s.text).join(" ")));
    if (text) {
      notes.push({
        id: `note-${groupStart}`,
        ts: formatTs(group[group.length - 1]?.ts),
        tsMs: groupTsMs(group),
        completed: groupPending(group) ? false : undefined,
        speaker: groupSpeaker,
        text,
        tags: extractNoteTags(text, entities, groupStart),
      });
    }
  };

  segs.forEach((segment, index) => {
    const speaker = speakerOf(segment);
    // A pending segment ends its group immediately, so it surfaces as its own live line.
    if (group.length && (speaker !== groupSpeaker || group.length >= CLUSTER || groupPending(group) || segment.completed === false)) {
      pushGroup(speaker !== groupSpeaker || groupPending(group));
      group = [];
    }
    if (!group.length) {
      groupStart = index;
      groupSpeaker = speaker;
    }
    group.push(segment);
  });
  pushGroup(true);
  return notes;
}

export function buildMeetingNotes(
  processedInput: ProcessedTranscriptNote[] | undefined,
  segmentInput: TranscriptSegment[] | undefined,
  entities: EntityItem[],
): MeetingNote[] {
  const processed = (Array.isArray(processedInput) ? processedInput : []).filter((note) => (note.text ?? "").trim());
  const segments = (Array.isArray(segmentInput) ? segmentInput : []).filter((s) => (s.text ?? "").trim());
  if (!processed.length) return fallbackMeetingNotes(segments, entities, new Set());

  const processedById = new Map(processed.filter((note) => note.id).map((note) => [note.id, note]));
  const consumed = new Set<string>();
  const notes: MeetingNote[] = [];
  let group: TranscriptSegment[] = [];
  let groupStart = 0;
  let groupSpeaker = "";

  const pushGroup = (force: boolean) => {
    // Always emit a PENDING (live, in-progress) tail so the in-flight line reaches the view.
    if (!group.length || (!force && !groupPending(group) && group.length < CLUSTER)) return;
    const text = titleCaseStart(condense(group.map((s) => s.text).join(" ")));
    if (text) {
      const index = notes.length;
      notes.push({
        id: `note-${groupStart}`,
        ts: formatTs(group[group.length - 1]?.ts),
        tsMs: groupTsMs(group),
        completed: groupPending(group) ? false : undefined,
        speaker: groupSpeaker,
        text,
        tags: extractNoteTags(text, entities, index),
      });
    }
  };

  segments.forEach((segment, index) => {
    const processedNote = segment.id ? processedById.get(segment.id) : undefined;
    if (processedNote) {
      pushGroup(true);
      group = [];
      const note = processedMeetingNote(processedNote, entities, notes.length);
      if (note) notes.push(note);
      consumed.add(processedNote.id);
      return;
    }
    const speaker = speakerOf(segment);
    // A pending segment ends its group immediately, so it surfaces as its own live line.
    if (group.length && (speaker !== groupSpeaker || group.length >= CLUSTER || groupPending(group) || segment.completed === false)) {
      pushGroup(speaker !== groupSpeaker || groupPending(group));
      group = [];
    }
    if (!group.length) {
      groupStart = index;
      groupSpeaker = speaker;
    }
    group.push(segment);
  });
  pushGroup(true);

  for (const note of processed) {
    if (note.id && consumed.has(note.id)) continue;
    const rendered = processedMeetingNote(note, entities, notes.length);
    if (rendered) notes.push(rendered);
  }
  return notes;
}

/** The live notes for the current meeting. Processed notes replace the exact live segment they cover;
 *  segments still waiting on the model remain visible through the local condensed fallback. */
export function useMeetingNotes(): MeetingNote[] {
  const meeting = useMeeting();
  const { segments } = useTranscript({ by: "time" });
  const entities = useEntities();
  return useMemo(() => {
    return buildMeetingNotes(meeting.transcript.notes, segments, entities);
  }, [meeting.transcript.notes, segments, entities]);
}
