/**
 * Shared hallucination filter — TypeScript version.
 *
 * Same logic as filter.py, reads the same phrase files.
 * Import from the bot's per-speaker pipeline.
 */

import * as fs from 'fs';
import * as path from 'path';

let phrases: Set<string> | null = null;

function loadPhrases(): Set<string> {
  if (phrases) return phrases;
  phrases = new Set();

  const dir = path.resolve(__dirname);
  try {
    for (const file of fs.readdirSync(dir)) {
      if (!file.endsWith('.txt')) continue;
      const content = fs.readFileSync(path.join(dir, file), 'utf-8');
      for (const line of content.split('\n')) {
        const t = line.trim();
        if (t && !t.startsWith('#')) phrases.add(t.toLowerCase());
      }
    }
  } catch { /* no phrase files available */ }

  return phrases;
}

export function isHallucination(
  text: string,
  options?: {
    compressionRatio?: number;
    noSpeechProb?: number;
    avgLogprob?: number;
  }
): boolean {
  if (!text?.trim()) return true;

  const trimmed = text.trim();
  const lower = trimmed.toLowerCase();

  // Known phrase
  if (loadPhrases().has(lower)) return true;

  // Too short
  const words = trimmed.split(/\s+/);
  if (words.length <= 1 && trimmed.length < 10) return true;

  // Repetition: same 3-6 word phrase repeated 3+ times
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

  // High compression ratio
  if (options?.compressionRatio && options.compressionRatio > 2.0) return true;

  // No speech + low confidence
  if (options?.noSpeechProb && options?.avgLogprob) {
    if (options.noSpeechProb > 0.6 && options.avgLogprob < -1.0) return true;
  }

  return false;
}
