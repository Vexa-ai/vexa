"use client";
/** Live meeting notes condense the running transcript into attributed, first-person paragraphs: the
 *  speaker's own words, shortened and folded as turns complete, with keyword tags inline. */
import { useMemo } from "react";
import { useEntities, useMeeting, useTranscript } from "./useMeeting";
import { cleanTranscriptText, extractNotableNumberSpans } from "./textSignals";
import type { EntityItem, TranscriptSegment } from "./types";

export type NoteTagKind = "company" | "person" | "metric" | "money" | "signal";
export interface NoteTag { id: string; label: string; kind: NoteTagKind; context?: string; at?: number; end?: number }
export interface MeetingNote { id: string; ts?: string; speaker?: string; chapter?: string; text: string; tags: NoteTag[] }

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

function speakerOf(segment: TranscriptSegment | undefined): string {
  const speaker = (segment?.speaker ?? "").trim();
  return speaker || "Speaker";
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

// Runs of Capitalized words, including institution connectors like "University of Chicago".
const PROPER_RE = /\b[A-Z][a-zA-Z]*(?:[''][A-Za-z]+)?(?:\s+(?:(?:of|for|and|the|in|at|to|&)\s+)?[A-Z][a-zA-Z]*(?:[''][A-Za-z]+)?)*\b/g;
const SPECIAL_SIGNAL_RE = /\b(?:Series\s+[A-Z]|Mag\s+7|S&P(?:\s*500)?)\b/g;
const INSTITUTION_RE = /^(?:University|College|Institute|School|Center|Centre|Department|Hospital|Laboratory|Labs?|Federal Reserve|Bank)\b/i;

function isSentenceStart(text: string, at: number): boolean {
  for (let i = at - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === " " || ch === "\n" || ch === "\t") continue;
    return ch === "." || ch === "!" || ch === "?" || ch === '"' || ch === "—" || ch === "-";
  }
  return true;
}

function classifyProper(phrase: string): NoteTagKind {
  if (/^Series\s+[A-Z]\b/.test(phrase)) return "signal";
  if (INSTITUTION_RE.test(phrase)) return "company";
  if (phrase.includes(" ")) return "person";                 // "Shane Legg"
  if (/^[A-Z]{2,5}$/.test(phrase)) return "signal";          // "AGI", "API"
  return "company";                                          // "DeepMind", "Amazon"
}

function contextForProper(phrase: string, fallback?: string): string | undefined {
  if (/^Series\s+[A-Z]\b/.test(phrase)) return "Round";
  if (INSTITUTION_RE.test(phrase)) return "Institution";
  return fallback;
}

function overlaps(found: { at: number; end: number }[], at: number, end: number): boolean {
  return found.some((item) => at < item.end && end > item.at);
}

function titleCaseStart(text: string): string {
  return text.replace(/^[a-z]/, (ch) => ch.toUpperCase());
}

/** Pull keyword tags out of a note text alone (no dependency on the entity pass, so tags show up even on
 *  a fresh live meeting): proper nouns (companies/people/acronyms) and notable figures.
 *  Entities, when available, only enrich a matched proper noun with context. */
export function extractNoteTags(text: string, entities: EntityItem[], index: number): NoteTag[] {
  const found: { at: number; end: number; tag: Omit<NoteTag, "id"> }[] = [];
  const entityCtx = new Map<string, string>();
  for (const e of entities) {
    const name = cleanTranscriptText(e.name ?? "").toLowerCase();
    if (name && e.context) entityCtx.set(name, e.context);
  }

  for (const m of text.matchAll(SPECIAL_SIGNAL_RE)) {
    const at = m.index ?? 0;
    const label = m[0].trim();
    const context = /^Series\s+[A-Z]\b/.test(label) ? "Round" : "Market";
    found.push({ at, end: at + label.length, tag: { label, kind: "signal", context, at, end: at + label.length } });
  }

  for (const m of text.matchAll(PROPER_RE)) {
    const matchAt = m.index ?? 0;
    if (overlaps(found, matchAt, matchAt + m[0].length)) continue;
    // Strip leading/trailing stopword tokens ("And I" → dropped; "Now DeepMind" → "DeepMind").
    const words = m[0].trim().split(/\s+/);
    let startOff = 0;
    while (words.length && STOPWORDS.has(words[0].toLowerCase())) { startOff += words[0].length + 1; words.shift(); }
    while (words.length && STOPWORDS.has(words[words.length - 1].toLowerCase())) words.pop();
    if (!words.length) continue;
    const phrase = words.join(" ").replace(/['']s$/i, "");
    const at = matchAt + startOff;
    const end = at + phrase.length;
    const single = words.length === 1;
    const internalCaps = /[a-z][A-Z]/.test(phrase) || /^[A-Z]{2,5}$/.test(phrase);
    if (single && phrase.length <= 1) continue;
    // Drop bare sentence-start capitalization unless it's clearly a name (internal caps / acronym / multi-word).
    if (single && !internalCaps && startOff === 0 && isSentenceStart(text, at)) continue;
    if (overlaps(found, at, end)) continue;
    const context = contextForProper(phrase, entityCtx.get(phrase.toLowerCase()));
    found.push({ at, end, tag: { label: phrase, kind: classifyProper(phrase), context, at, end } });
  }

  for (const signal of extractNotableNumberSpans(text)) {
    const at = signal.at ?? 0;
    const label = signal.text.trim();
    const end = signal.end ?? at + label.length;
    if (!label || overlaps(found, at, end)) continue;
    found.push({
      at,
      end,
      tag: {
        label,
        kind: signal.context === "Money" ? "money" : "metric",
        context: signal.context === "Money" ? undefined : signal.context,
        at,
        end,
      },
    });
  }

  found.sort((a, b) => a.at - b.at);
  const acc: NoteTag[] = [];
  const seen = new Set<string>();
  for (const f of found) pushTag(acc, seen, f.tag, index);
  return acc;
}

/** The live notes for the current meeting. Completed speaker turns fold immediately; a continuing turn
 *  folds after a few utterances, and the newest partial turn remains visible in the raw tail. */
export function useMeetingNotes(): MeetingNote[] {
  const meeting = useMeeting();
  const { segments } = useTranscript({ by: "time" });
  const entities = useEntities();
  return useMemo(() => {
    const processed = Array.isArray(meeting.transcript.notes) ? meeting.transcript.notes : [];
    if (processed.length) {
      return processed
        .filter((note) => (note.text ?? "").trim())
        .map((note, index) => {
          const text = titleCaseStart(condense(note.text));
          return {
            id: note.id || `processed-${index}`,
            ts: formatTs(note.ts),
            speaker: speakerOf(note),
            chapter: cleanTranscriptText(note.chapter ?? ""),
            text,
            tags: extractNoteTags(text, entities, index),
          };
        });
    }
    const segs = (Array.isArray(segments) ? segments : []).filter((s: TranscriptSegment) => (s.text ?? "").trim());
    const notes: MeetingNote[] = [];
    let group: TranscriptSegment[] = [];
    let groupStart = 0;
    let groupSpeaker = "";

    const pushGroup = (force: boolean) => {
      if (!group.length || (!force && group.length < CLUSTER)) return;
      const text = titleCaseStart(condense(group.map((s) => s.text).join(" ")));
      if (text) {
        notes.push({
          id: `note-${groupStart}`,
          ts: formatTs(group[group.length - 1]?.ts),
          speaker: groupSpeaker,
          text,
          tags: extractNoteTags(text, entities, groupStart),
        });
      }
    };

    segs.forEach((segment, index) => {
      const speaker = speakerOf(segment);
      if (group.length && (speaker !== groupSpeaker || group.length >= CLUSTER)) {
        pushGroup(speaker !== groupSpeaker);
        group = [];
      }
      if (!group.length) {
        groupStart = index;
        groupSpeaker = speaker;
      }
      group.push(segment);
    });
    pushGroup(false);
    return notes;
  }, [meeting.transcript.notes, segments, entities]);
}
