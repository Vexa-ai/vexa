/**
 * Hallucination filter for post-transcription filtering.
 *
 * Catches known hallucination phrases, repetition loops, and junk output before it
 * reaches the transcript. Phrase files loaded from hallucinations/*.txt (shipped to
 * dist/ by the build). ESM: the dir is resolved from import.meta.url, so it works
 * the same run-from-src (tsx) and run-from-dist (built).
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { log } from './log.js';

const here = dirname(fileURLToPath(import.meta.url));
let phrases: Set<string> | null = null;

function loadPhrases(): Set<string> {
  if (phrases) return phrases;
  phrases = new Set();

  const dir = resolve(here, 'hallucinations');   // src/hallucinations (tsx) ‖ dist/hallucinations (built)
  try {
    if (existsSync(dir)) {
      for (const file of readdirSync(dir)) {
        if (!file.endsWith('.txt')) continue;
        const content = readFileSync(join(dir, file), 'utf-8');
        for (const line of content.split('\n')) {
          const t = line.trim();
          if (t && !t.startsWith('#')) phrases.add(t.toLowerCase());
        }
      }
    }
  } catch { /* fall through to the empty-set warning */ }

  if (phrases.size > 0) log(`[HallucinationFilter] Loaded ${phrases.size} phrases from ${dir}`);
  else log('[HallucinationFilter] WARNING: No phrase files found');
  return phrases;
}

/** CREDIT LINES — subtitle and caption credits Whisper learned from subtitled video, with whatever
 *  names it attaches. Never speech, whatever the audio did, so they are dropped on a voiced window
 *  too. (A real call, 2026-10-14: "Редактор субтитров А.Семкин Корректор А.Егорова" mid-meeting.)
 *  No `\b` next to Cyrillic: JS word boundaries are ASCII-only. */
const CREDIT_PATTERNS: RegExp[] = [
  /(?:^|[\s.,])(?:редактор|корректор)(?:ы)?\s+субтитров(?:$|[\s.,!])/i,
  /(?:^|[\s.,])субтитры\s+(?:сделал|сделала|делал|делала|создавал|создал|создала|подогнал|подготовил|подготовила|сделаны|выполнил|перевёл|перевел)(?:$|[\s.,!])/i,
  /\bsubtitles?\s+(?:by|made by|created by|edited by)\b/i,
  /\bamara\.org\b/i,
];

/** OUTROS — sign-offs from the end of a video. People do say some of these, so they are dropped
 *  only from a window with no speech energy behind it (`voiced` false or unknown). */
const OUTRO_PATTERNS: RegExp[] = [
  /^(?:thanks|thank you)(?:\s+(?:so|very)\s+much)?\s+for\s+watching\b/i,
  /^(?:please\s+)?(?:like\s+and\s+)?subscribe\b/i,
  /^спасибо\s+за\s+(?:просмотр|внимание)[.!…]*$/i,
  /^продолжение\s+следует[.!…]*$/i,
  /^vielen\s+dank\s+fürs\s+zuschauen/i,
  /^obrigad[oa]\s+por\s+assistir/i,
];

/**
 * Returns true if the text is a hallucination and should be dropped.
 */
/** `voiced`: the window this text came from carries speech-level audio. A short answer on such a
 *  window is a real short utterance ("Да.", "Один."), not junk, so the short-text rule is skipped. */
export function isHallucination(text: string, opts: { voiced?: boolean } = {}): boolean {
  if (!text?.trim()) return true;

  const trimmed = text.trim();
  const lower = trimmed.toLowerCase();

  // Known phrase (exact match, then retry with normalized punctuation)
  const db = loadPhrases();
  if (db.has(lower)) return true;
  const stripped = lower.replace(/[.!?…]+$/g, '').replace(/\.{2,}$/g, '');
  if (stripped !== lower && db.has(stripped)) return true;
  if (stripped !== lower && db.has(stripped + '...')) return true;
  if (stripped !== lower && db.has(stripped + '.')) return true;

  // Credit lines (always) and outros (only where no speech energy supports them)
  if (CREDIT_PATTERNS.some((re) => re.test(trimmed))) return true;
  if (!opts.voiced && OUTRO_PATTERNS.some((re) => re.test(trimmed))) return true;

  // Too short (single word < 10 chars)
  const words = trimmed.split(/\s+/);
  if (!opts.voiced && words.length <= 1 && trimmed.length < 10) return true;

  // Repetition loop: same 3-6 word phrase repeated 3+ times
  if (words.length >= 9) {
    for (let len = 3; len <= 6; len++) {
      const phrase = words.slice(0, len).join(' ').toLowerCase();
      let count = 0;
      for (let i = 0; i <= words.length - len; i += len) {
        if (words.slice(i, i + len).join(' ').toLowerCase() === phrase) count++;
      }
      if (count >= 3) return true;
    }
  }

  return false;
}

// The low-confidence STT-segment filter (isLowConfidenceSegment) lives in
// @vexa/transcribe-whisper (src/confidence.ts) — it belongs at the stt.v1 egress,
// applied to Whisper's raw output before the confirm loop. This module keeps only
// the phrase-list hallucination filter (isHallucination), a downstream concern.
