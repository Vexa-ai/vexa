/**
 * Hallucination filter for post-transcription filtering.
 *
 * Catches known hallucination phrases, repetition loops, and junk output
 * before publishing to Redis. Phrase files loaded from hallucinations/*.txt.
 */

import * as fs from 'fs';
import * as path from 'path';
import { log } from './log';

let phrases: Set<string> | null = null;

function loadPhrases(): Set<string> {
  if (phrases) return phrases;
  phrases = new Set();

  // Try multiple possible locations (dist vs src, Docker vs local)
  const candidates = [
    path.resolve(__dirname, 'hallucinations'),
    path.resolve(__dirname, '..', 'services', 'hallucinations'),
    path.resolve(__dirname, '..', '..', 'src', 'services', 'hallucinations'),
  ];

  for (const dir of candidates) {
    try {
      if (!fs.existsSync(dir)) continue;
      for (const file of fs.readdirSync(dir)) {
        if (!file.endsWith('.txt')) continue;
        const content = fs.readFileSync(path.join(dir, file), 'utf-8');
        for (const line of content.split('\n')) {
          const t = line.trim();
          if (t && !t.startsWith('#')) phrases.add(t.toLowerCase());
        }
      }
      if (phrases.size > 0) {
        log(`[HallucinationFilter] Loaded ${phrases.size} phrases from ${dir}`);
        break;
      }
    } catch { /* try next */ }
  }

  if (phrases.size === 0) {
    log('[HallucinationFilter] WARNING: No phrase files found');
  }

  return phrases;
}

/**
 * Returns true if the text is a hallucination and should be dropped.
 */
export function isHallucination(text: string): boolean {
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

  // Too short (single word < 10 chars)
  const words = trimmed.split(/\s+/);
  if (words.length <= 1 && trimmed.length < 10) return true;

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

/**
 * Confidence filter — drop a Whisper segment the model is clearly GUESSING. This is
 * the source of the "[You] お疲れ様でした"-style phantom lines (hallucinated over near-
 * silence) and faint cross-channel bleed (a neighbouring speaker leaking onto this
 * channel at low energy). Uses faster-whisper's own per-segment signals:
 *  - non-speech: high no_speech_prob WITH low avg_logprob = invented over silence,
 *  - repetition: a blown-up compression_ratio = looped/garbage text,
 *  - very low avg_logprob alone = the model is guessing.
 * Thresholds are conservative so real (even quiet) speech survives.
 */
export function isLowConfidenceSegment(s: { avg_logprob?: number; no_speech_prob?: number; compression_ratio?: number }): boolean {
  if (s.no_speech_prob !== undefined && s.avg_logprob !== undefined && s.no_speech_prob > 0.6 && s.avg_logprob < -1.0) return true;
  if (s.compression_ratio !== undefined && s.compression_ratio > 2.4) return true;
  if (s.avg_logprob !== undefined && s.avg_logprob < -1.3) return true;
  return false;
}
