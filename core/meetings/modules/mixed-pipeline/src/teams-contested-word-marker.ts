import type { TranscriptionSegment } from '@vexa/transcribe-whisper';

export interface TeamsContestRow {
  csrc: number;
  sourceKey: string;
  segmentId: string;
  text: string;
  startMs: number;
  endMs: number;
  completed: boolean;
}

export interface TeamsContestSubmission {
  sourceKey: string;
  segments: TranscriptionSegment[];
  /** Pipeline-only retention clock; the detector deliberately ignores it. */
  observedAtMs?: number;
}

export interface TeamsRoutedSpan {
  csrc: number;
  startMs: number;
  endMs: number;
}

export interface TeamsWordContest {
  leftSegmentId: string;
  rightSegmentId: string;
  leftCsrc: number;
  rightCsrc: number;
  leftTokenStart: number;
  rightTokenStart: number;
  tokenLength: number;
  contestedText: string;
  sharedRoutedMs: number;
  medianWordDeltaMs: number;
}

export interface TeamsContestConfig {
  minSharedRoutedMs?: number;
  minMatchedTokens?: number;
  minMatchedCharacters?: number;
  minSingleMatchedCharacters?: number;
  maxMedianWordDeltaMs?: number;
}

interface Token {
  raw: string;
  normalized: string;
  start: number;
  end: number;
}

interface WordInterval {
  startMs: number;
  endMs: number;
}

interface WordTrace {
  mapped: Map<number, WordInterval>;
}

const normalize = (value: unknown): string => String(value ?? '')
  .toLocaleLowerCase()
  .replace(/[’]/g, "'")
  .replace(/[^\p{L}\p{N}']/gu, '');

export function tokenizeTeamsContestText(text: string): Token[] {
  const tokens: Token[] = [];
  const pattern = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;
  for (const match of text.matchAll(pattern)) {
    const raw = match[0];
    const normalized = normalize(raw);
    if (!normalized) continue;
    tokens.push({ raw, normalized, start: match.index, end: match.index + raw.length });
  }
  return tokens;
}

function longestCommonContiguous(left: Token[], right: Token[]): {
  leftStart: number;
  rightStart: number;
  length: number;
} {
  let best = { leftStart: 0, rightStart: 0, length: 0 };
  let previous = new Uint16Array(right.length + 1);
  for (let leftIndex = 0; leftIndex < left.length; leftIndex++) {
    const current = new Uint16Array(right.length + 1);
    for (let rightIndex = 0; rightIndex < right.length; rightIndex++) {
      if (left[leftIndex].normalized !== right[rightIndex].normalized) continue;
      const length = previous[rightIndex] + 1;
      current[rightIndex + 1] = length;
      if (length > best.length) {
        best = { leftStart: leftIndex - length + 1, rightStart: rightIndex - length + 1, length };
      }
    }
    previous = current;
  }
  return best;
}

const median = (values: number[]): number => {
  if (!values.length) return Number.POSITIVE_INFINITY;
  const ordered = values.slice().sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
};

const flattenedWords = (segments: TranscriptionSegment[]): Array<{
  normalized: string;
  start: number;
  end: number;
}> => segments.flatMap((segment) => (segment.words ?? [])
  .map((word) => ({ normalized: normalize(word.word), start: Number(word.start), end: Number(word.end) }))
  .filter((word) => word.normalized && Number.isFinite(word.start) && Number.isFinite(word.end)));

function consensusWordTrace(row: TeamsContestRow, submissions: TeamsContestSubmission[]): WordTrace | null {
  const rowTokens = tokenizeTeamsContestText(row.text);
  const candidates: Array<Map<number, WordInterval>> = [];
  for (const submission of submissions) {
    const words = flattenedWords(submission.segments);
    if (!words.length) continue;
    const wordTokens: Token[] = words.map((word) => ({
      raw: word.normalized,
      normalized: word.normalized,
      start: 0,
      end: word.normalized.length,
    }));
    const match = longestCommonContiguous(rowTokens, wordTokens);
    const minimumTraceTokens = Math.min(3, rowTokens.length);
    if (match.leftStart !== 0 || match.length < Math.max(minimumTraceTokens, Math.ceil(rowTokens.length * 0.6))) {
      continue;
    }
    const baseMs = row.startMs - words[match.rightStart].start * 1000;
    const mapped = new Map<number, WordInterval>();
    for (let index = 0; index < match.length; index++) {
      const word = words[match.rightStart + index];
      mapped.set(index, { startMs: baseMs + word.start * 1000, endMs: baseMs + word.end * 1000 });
    }
    candidates.push(mapped);
  }
  if (!candidates.length) return null;
  const mapped = new Map<number, WordInterval>();
  for (let tokenIndex = 0; tokenIndex < rowTokens.length; tokenIndex++) {
    const starts = candidates.map((candidate) => candidate.get(tokenIndex)?.startMs)
      .filter((value): value is number => Number.isFinite(value));
    const ends = candidates.map((candidate) => candidate.get(tokenIndex)?.endMs)
      .filter((value): value is number => Number.isFinite(value));
    if (starts.length && ends.length) mapped.set(tokenIndex, { startMs: median(starts), endMs: median(ends) });
  }
  return { mapped };
}

function phraseInterval(trace: WordTrace, start: number, length: number): WordInterval | null {
  const words = Array.from({ length }, (_, index) => trace.mapped.get(start + index));
  if (words.some((word) => !word)) return null;
  return { startMs: words[0]!.startMs, endMs: words[words.length - 1]!.endMs };
}

function mergedIntervals(spans: TeamsRoutedSpan[], csrc: number): Array<{ start: number; end: number }> {
  const rows = spans.filter((span) => span.csrc === csrc)
    .map((span) => ({ start: span.startMs, end: span.endMs }))
    .filter((span) => Number.isFinite(span.start) && Number.isFinite(span.end) && span.end >= span.start)
    .sort((left, right) => left.start - right.start);
  const merged: Array<{ start: number; end: number }> = [];
  for (const row of rows) {
    const last = merged[merged.length - 1];
    if (last && row.start <= last.end + 20) last.end = Math.max(last.end, row.end);
    else merged.push({ ...row });
  }
  return merged;
}

function sharedRoutedMs(
  spans: TeamsRoutedSpan[],
  leftCsrc: number,
  rightCsrc: number,
  floor: number,
  ceiling: number,
): number {
  const left = mergedIntervals(spans, leftCsrc);
  const right = mergedIntervals(spans, rightCsrc);
  let leftIndex = 0;
  let rightIndex = 0;
  let total = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    total += Math.max(0, Math.min(left[leftIndex].end, right[rightIndex].end, ceiling)
      - Math.max(left[leftIndex].start, right[rightIndex].start, floor));
    if (left[leftIndex].end < right[rightIndex].end) leftIndex++;
    else rightIndex++;
  }
  return total;
}

export function detectTeamsWordContest(
  left: TeamsContestRow,
  right: TeamsContestRow,
  submissions: TeamsContestSubmission[],
  spans: TeamsRoutedSpan[],
  options: TeamsContestConfig = {},
): TeamsWordContest | null {
  if (!left.completed || !right.completed || left.csrc === right.csrc) return null;
  const floor = Math.max(left.startMs, right.startMs);
  const ceiling = Math.min(left.endMs, right.endMs);
  if (!(ceiling > floor)) return null;
  const config = {
    minSharedRoutedMs: options.minSharedRoutedMs ?? 250,
    minMatchedTokens: options.minMatchedTokens ?? 2,
    minMatchedCharacters: options.minMatchedCharacters ?? 10,
    minSingleMatchedCharacters: options.minSingleMatchedCharacters ?? 6,
    maxMedianWordDeltaMs: options.maxMedianWordDeltaMs ?? 1500,
  };
  const routedMs = sharedRoutedMs(spans, left.csrc, right.csrc, floor, ceiling);
  if (routedMs < config.minSharedRoutedMs) return null;

  const leftTokens = tokenizeTeamsContestText(left.text);
  const rightTokens = tokenizeTeamsContestText(right.text);
  const match = longestCommonContiguous(leftTokens, rightTokens);
  const contestedText = match.length
    ? left.text.slice(leftTokens[match.leftStart].start, leftTokens[match.leftStart + match.length - 1].end)
    : '';
  const matchedCharacters = contestedText.replace(/\s+/g, '').length;
  const ordinaryMatch = match.length >= config.minMatchedTokens && matchedCharacters >= config.minMatchedCharacters;
  const exactSingleWord = match.length === 1 && leftTokens.length === 1 && rightTokens.length === 1
    && matchedCharacters >= config.minSingleMatchedCharacters;
  if (!ordinaryMatch && !exactSingleWord) return null;

  const leftTrace = consensusWordTrace(left, submissions.filter((row) => row.sourceKey === left.sourceKey));
  const rightTrace = consensusWordTrace(right, submissions.filter((row) => row.sourceKey === right.sourceKey));
  if (!leftTrace || !rightTrace) return null;
  const deltas: number[] = [];
  for (let index = 0; index < match.length; index++) {
    const leftWord = leftTrace.mapped.get(match.leftStart + index);
    const rightWord = rightTrace.mapped.get(match.rightStart + index);
    if (!leftWord || !rightWord) continue;
    deltas.push(Math.abs((leftWord.startMs + leftWord.endMs - rightWord.startMs - rightWord.endMs) / 2));
  }
  const medianWordDeltaMs = median(deltas);
  if (deltas.length < match.length || medianWordDeltaMs > config.maxMedianWordDeltaMs) return null;
  if (!phraseInterval(leftTrace, match.leftStart, match.length)
      || !phraseInterval(rightTrace, match.rightStart, match.length)) return null;

  return {
    leftSegmentId: left.segmentId,
    rightSegmentId: right.segmentId,
    leftCsrc: left.csrc,
    rightCsrc: right.csrc,
    leftTokenStart: match.leftStart,
    rightTokenStart: match.rightStart,
    tokenLength: match.length,
    contestedText,
    sharedRoutedMs: Math.round(routedMs),
    medianWordDeltaMs: Math.round(medianWordDeltaMs),
  };
}

function wrapTokenRange(text: string, tokenStart: number, tokenLength: number): string {
  const tokens = tokenizeTeamsContestText(text);
  if (!tokens[tokenStart] || !tokens[tokenStart + tokenLength - 1]) return text;
  const start = tokens[tokenStart].start;
  const end = tokens[tokenStart + tokenLength - 1].end;
  return `${text.slice(0, start)}[${text.slice(start, end)}]${text.slice(end)}`;
}

export function markTeamsWordContests(row: TeamsContestRow, contests: TeamsWordContest[]): string {
  const relevant = contests.filter((contest) => contest.leftSegmentId === row.segmentId
    || contest.rightSegmentId === row.segmentId);
  const ranges = relevant.map((contest) => contest.leftSegmentId === row.segmentId
    ? { start: contest.leftTokenStart, length: contest.tokenLength }
    : { start: contest.rightTokenStart, length: contest.tokenLength })
    .sort((left, right) => right.start - left.start || right.length - left.length);
  let text = row.text;
  let lastStart = Number.POSITIVE_INFINITY;
  for (const range of ranges) {
    if (range.start + range.length > lastStart) continue;
    text = wrapTokenRange(text, range.start, range.length);
    lastStart = range.start;
  }
  return text;
}
