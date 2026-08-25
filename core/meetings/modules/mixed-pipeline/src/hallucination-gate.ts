/**
 * hallucination-gate — the mixed lane's suppression of STT output that no one said.
 *
 * Whisper invents text on non-speech, and the lane's windows are short, so it invents more
 * than a one-shot transcription does. Measured on a 270s clean fixture: four rows of
 * "Субтитры сделал DimaTorzok" / "Продолжение следует" that the same file transcribed straight
 * through never produced. Those windows carry HEALTHY metrics (no_speech_prob 0.0,
 * avg_logprob -0.31) and their energy matches real speech — the fixture's non-speech is music,
 * louder than the talking — so neither the confidence gates nor an RMS floor can see them.
 * What identifies them is the STRING.
 *
 * Matching semantics are the gmeet lane's (`gmeet-pipeline/src/hallucination-filter.ts`):
 * exact, then punctuation-normalised, plus the repetition-loop rule. The PHRASE SET is not:
 * the gmeet list carries "Yeah.", "Okay.", "I don't know." — on the m24 Teams tape those
 * suppress real speech, and this lane's rows are a one-shot mixed stream with nothing to
 * re-derive them from. `hallucinations/mixed.txt` therefore holds only the media-artifact
 * family, and pins its own subset relationship to the gmeet files in a test.
 *
 * The two lanes cannot import each other (gate:isolation, scripts/check-isolation.js), which
 * is why the semantics are restated here rather than imported.
 *
 * Off switch: VEXA_MIXED_HALLUCINATION_GATE=off. A suppression is never silent — the caller
 * receives a typed observation and the lane logs it.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export type HallucinationRule = 'phrase' | 'pattern' | 'repetition';

export interface SuppressedSegment {
  text: string;
  startMs: number;
  endMs: number;
  rule: HallucinationRule;
}

const here = dirname(fileURLToPath(import.meta.url));
let phrases: Set<string> | null = null;
let patterns: RegExp[] = [];

function load(): Set<string> {
  if (phrases) return phrases;
  phrases = new Set();
  const dir = resolve(here, 'hallucinations');   // src/hallucinations (tsx) ‖ dist/hallucinations (built)
  try {
    if (existsSync(dir)) {
      for (const file of readdirSync(dir)) {
        if (!file.endsWith('.txt')) continue;
        for (const line of readFileSync(join(dir, file), 'utf-8').split('\n')) {
          const t = line.trim();
          if (!t || t.startsWith('#')) continue;
          if (t.startsWith('re:')) { try { patterns.push(new RegExp(t.slice(3), 'i')); } catch { /* skip a bad line, never crash the lane */ } }
          else phrases.add(t.toLowerCase());
        }
      }
    }
  } catch { /* an unreadable list means no gate, never a failed turn */ }
  return phrases;
}

/** Same repetition-loop rule as the gmeet filter: a 3–6 word phrase repeated 3+ times.
 *  Language-agnostic and structural, so it carries over unchanged. */
function repetitionLoop(text: string): boolean {
  const words = text.trim().split(/\s+/);
  if (words.length < 9) return false;
  for (let len = 3; len <= 6; len++) {
    const phrase = words.slice(0, len).join(' ').toLowerCase();
    let count = 0;
    for (let i = 0; i <= words.length - len; i += len) {
      if (words.slice(i, i + len).join(' ').toLowerCase() === phrase) count++;
    }
    if (count >= 3) return true;
  }
  return false;
}

/**
 * A growing Whisper window may begin with a genuine repeated acknowledgement and then continue
 * with minutes of real speech. The legacy repetition rule intentionally spots a loop at the
 * beginning, but condemning the WHOLE growing window for that prefix creates a transcript gap.
 * Teams' GMeet-compatible window therefore suppresses repetition only when the repeated prefix
 * accounts for at least 80% of the complete result. Exact media-artifact phrases and patterns
 * remain fail-closed through `hallucinationRule`.
 */
export function teamsWindowHallucinationRule(text: string): HallucinationRule | null {
  const rule = hallucinationRule(text);
  if (rule !== 'repetition') return rule;

  const words = text.trim().split(/\s+/).filter(Boolean);
  for (let len = 3; len <= 6; len++) {
    const phrase = words.slice(0, len).join(' ').toLowerCase();
    let count = 0;
    for (let i = 0; i <= words.length - len; i += len) {
      if (words.slice(i, i + len).join(' ').toLowerCase() !== phrase) break;
      count++;
    }
    if (count >= 3 && (count * len) / words.length >= 0.8) return 'repetition';
  }
  return null;
}

/** Which rule condemns this text, or null to publish it. Deliberately NOT included: the gmeet
 *  filter's "single word under 10 chars" rule — on the m24 tape that eats "Yeah.", "Yep.",
 *  "Right." and the rest of a real meeting's backchannel. */
export function hallucinationRule(text: string): HallucinationRule | null {
  if (typeof process !== 'undefined' && process.env?.VEXA_MIXED_HALLUCINATION_GATE === 'off') return null;
  const trimmed = (text ?? '').trim();
  if (!trimmed) return null;   // empty text is the caller's business, not a hallucination
  const db = load();
  const lower = trimmed.toLowerCase();
  if (db.has(lower)) return 'phrase';
  const stripped = lower.replace(/[.!?…]+$/g, '').replace(/\.{2,}$/g, '');
  if (stripped !== lower && (db.has(stripped) || db.has(stripped + '.') || db.has(stripped + '...'))) return 'phrase';
  for (const re of patterns) if (re.test(trimmed)) return 'pattern';
  if (repetitionLoop(trimmed)) return 'repetition';
  return null;
}

/** Test seam: forget the loaded lists (so a test can point the loader at a different dir). */
export function resetHallucinationCache(): void { phrases = null; patterns = []; }
