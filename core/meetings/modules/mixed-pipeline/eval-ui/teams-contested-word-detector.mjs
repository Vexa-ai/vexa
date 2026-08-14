export const AUTO_WORD_CONTEST_KIND = 'teams-csrc-word-contest-annotations-v1';

const normalize = (value) => String(value ?? '')
  .toLocaleLowerCase()
  .replace(/[’]/g, "'")
  .replace(/[^\p{L}\p{N}']/gu, '');

export function tokenizeText(text) {
  const tokens = [];
  const pattern = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;
  for (const match of String(text ?? '').matchAll(pattern)) {
    tokens.push({ raw: match[0], normalized: normalize(match[0]), start: match.index, end: match.index + match[0].length });
  }
  return tokens.filter((token) => token.normalized);
}

export function longestCommonContiguous(left, right) {
  let best = { leftStart: 0, rightStart: 0, length: 0 };
  let previous = new Uint16Array(right.length + 1);
  for (let leftIndex = 0; leftIndex < left.length; leftIndex++) {
    const current = new Uint16Array(right.length + 1);
    for (let rightIndex = 0; rightIndex < right.length; rightIndex++) {
      if (left[leftIndex].normalized !== right[rightIndex].normalized) continue;
      const length = previous[rightIndex] + 1;
      current[rightIndex + 1] = length;
      if (length > best.length) best = { leftStart: leftIndex - length + 1, rightStart: rightIndex - length + 1, length };
    }
    previous = current;
  }
  return best;
}

const flattenWords = (segments) => (segments ?? []).flatMap((segment) => (segment.words ?? [])
  .map((word) => ({
    normalized: normalize(word.word),
    start: Number(word.start),
    end: Number(word.end),
  }))
  .filter((word) => word.normalized && Number.isFinite(word.start) && Number.isFinite(word.end)));

const median = (values) => {
  if (!values.length) return Number.POSITIVE_INFINITY;
  const ordered = values.slice().sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
};

function consensusWordTrace(row, submissions) {
  const rowTokens = tokenizeText(row.text);
  const candidates = [];
  for (const submission of submissions) {
    const words = flattenWords(submission.segments);
    if (!words.length) continue;
    const match = longestCommonContiguous(rowTokens, words);
    const minimumTraceTokens = Math.min(3, rowTokens.length);
    if (match.leftStart !== 0 || match.length < Math.max(minimumTraceTokens, Math.ceil(rowTokens.length * 0.6))) continue;
    const baseMs = Number(row.startMs) - words[match.rightStart].start * 1000;
    const mapped = new Map();
    for (let index = 0; index < match.length; index++) {
      const word = words[match.rightStart + index];
      mapped.set(index, { startMs: baseMs + word.start * 1000, endMs: baseMs + word.end * 1000 });
    }
    candidates.push({ mapped, submissionHash: submission.hash ?? null });
  }
  if (!candidates.length) return null;
  const mapped = new Map();
  for (let tokenIndex = 0; tokenIndex < rowTokens.length; tokenIndex++) {
    const starts = candidates.map((candidate) => candidate.mapped.get(tokenIndex)?.startMs).filter(Number.isFinite);
    const ends = candidates.map((candidate) => candidate.mapped.get(tokenIndex)?.endMs).filter(Number.isFinite);
    if (!starts.length || !ends.length) continue;
    mapped.set(tokenIndex, { startMs: median(starts), endMs: median(ends), confirmations: starts.length });
  }
  return {
    mapped,
    traceCount: candidates.length,
    submissionHashes: [...new Set(candidates.map((candidate) => candidate.submissionHash).filter(Boolean))],
  };
}

function phraseInterval(trace, start, length) {
  const words = Array.from({ length }, (_, index) => trace.mapped.get(start + index)).filter(Boolean);
  if (words.length !== length) return null;
  return { startMs: words[0].startMs, endMs: words.at(-1).endMs };
}

function mergedIntervals(spans, csrc) {
  const rows = spans.filter((span) => Number(span.csrc) === Number(csrc))
    .map((span) => ({ start: Number(span.startMs), end: Number(span.endMs) }))
    .filter((span) => Number.isFinite(span.start) && Number.isFinite(span.end) && span.end >= span.start)
    .sort((left, right) => left.start - right.start);
  const merged = [];
  for (const row of rows) {
    const last = merged.at(-1);
    if (last && row.start <= last.end + 20) last.end = Math.max(last.end, row.end);
    else merged.push({ ...row });
  }
  return merged;
}

function sharedRoutedMs(spansByCsrc, left, right, floor, ceiling) {
  const leftSpans = spansByCsrc.get(left) ?? [];
  const rightSpans = spansByCsrc.get(right) ?? [];
  let leftIndex = 0;
  let rightIndex = 0;
  let total = 0;
  while (leftIndex < leftSpans.length && rightIndex < rightSpans.length) {
    const leftSpan = leftSpans[leftIndex];
    const rightSpan = rightSpans[rightIndex];
    total += Math.max(0, Math.min(leftSpan.end, rightSpan.end, ceiling) - Math.max(leftSpan.start, rightSpan.start, floor));
    if (leftSpan.end < rightSpan.end) leftIndex++;
    else rightIndex++;
  }
  return total;
}

export function detectContestedWords(result, options = {}) {
  const config = {
    minSharedRoutedMs: options.minSharedRoutedMs ?? 250,
    minMatchedTokens: options.minMatchedTokens ?? 2,
    minMatchedCharacters: options.minMatchedCharacters ?? 10,
    minSingleMatchedCharacters: options.minSingleMatchedCharacters ?? 6,
    maxMedianWordDeltaMs: options.maxMedianWordDeltaMs ?? 1500,
  };
  const rows = result?.candidate?.confirmed ?? [];
  const submissions = result?.candidate?.submissions ?? [];
  const acceptedSpans = result?.candidate?.acceptedSpans ?? [];
  const submissionsBySource = new Map();
  for (const submission of submissions) {
    if (!submission.sourceKey || !submission.segments?.length) continue;
    const list = submissionsBySource.get(submission.sourceKey) ?? [];
    list.push(submission);
    submissionsBySource.set(submission.sourceKey, list);
  }
  const spansByCsrc = new Map();
  for (const csrc of new Set(acceptedSpans.map((span) => Number(span.csrc)))) {
    spansByCsrc.set(csrc, mergedIntervals(acceptedSpans, csrc));
  }
  const traces = new Map(rows.map((row) => [String(row.segmentId), consensusWordTrace(row, submissionsBySource.get(row.sourceKey) ?? [])]));
  const annotations = new Map();
  const rejected = { noRoutedOverlap: 0, weakText: 0, noWordTrace: 0, distantWords: 0 };
  let pairsConsidered = 0;

  for (let leftIndex = 0; leftIndex < rows.length; leftIndex++) {
    const left = rows[leftIndex];
    const leftTokens = tokenizeText(left.text);
    for (let rightIndex = leftIndex + 1; rightIndex < rows.length; rightIndex++) {
      const right = rows[rightIndex];
      if (Number(left.csrc) === Number(right.csrc)) continue;
      const floor = Math.max(Number(left.startMs), Number(right.startMs));
      const ceiling = Math.min(Number(left.endMs), Number(right.endMs));
      if (!(ceiling > floor)) continue;
      pairsConsidered++;
      const routedMs = sharedRoutedMs(spansByCsrc, Number(left.csrc), Number(right.csrc), floor, ceiling);
      if (routedMs < config.minSharedRoutedMs) { rejected.noRoutedOverlap++; continue; }

      const rightTokens = tokenizeText(right.text);
      const match = longestCommonContiguous(leftTokens, rightTokens);
      const contestedText = match.length
        ? String(left.text).slice(leftTokens[match.leftStart].start, leftTokens[match.leftStart + match.length - 1].end)
        : '';
      const matchedCharacters = contestedText.replace(/\s+/g, '').length;
      const normalMatch = match.length >= config.minMatchedTokens && matchedCharacters >= config.minMatchedCharacters;
      const exactSingleWord = match.length === 1 && leftTokens.length === 1 && rightTokens.length === 1
        && matchedCharacters >= config.minSingleMatchedCharacters;
      if (!normalMatch && !exactSingleWord) { rejected.weakText++; continue; }

      const leftTrace = traces.get(String(left.segmentId));
      const rightTrace = traces.get(String(right.segmentId));
      if (!leftTrace || !rightTrace) { rejected.noWordTrace++; continue; }
      const deltas = [];
      for (let index = 0; index < match.length; index++) {
        const leftWord = leftTrace.mapped.get(match.leftStart + index);
        const rightWord = rightTrace.mapped.get(match.rightStart + index);
        if (!leftWord || !rightWord) continue;
        deltas.push(Math.abs((leftWord.startMs + leftWord.endMs - rightWord.startMs - rightWord.endMs) / 2));
      }
      const medianWordDeltaMs = median(deltas);
      if (deltas.length < match.length || medianWordDeltaMs > config.maxMedianWordDeltaMs) {
        rejected.distantWords++;
        continue;
      }

      const leftInterval = phraseInterval(leftTrace, match.leftStart, match.length);
      const rightInterval = phraseInterval(rightTrace, match.rightStart, match.length);
      if (!leftInterval || !rightInterval) { rejected.noWordTrace++; continue; }
      const consensusStartMs = Math.round(median([leftInterval.startMs, rightInterval.startMs]));
      const consensusEndMs = Math.round(median([leftInterval.endMs, rightInterval.endMs]));
      const pairKey = [String(left.segmentId), String(right.segmentId)].sort().join('~');
      annotations.set(pairKey, {
        segmentId: String(left.segmentId),
        rivalSegmentId: String(right.segmentId),
        rivalCsrc: Number(right.csrc),
        contestedText,
        reason: `Automatic detector: ${Math.round(routedMs)} ms shared routed PCM; ${match.length} identical contiguous words; ${Math.round(medianWordDeltaMs)} ms median word-time delta. Speaker ownership intentionally unresolved.`,
        evidence: {
          detector: 'shared-routed-pcm+word-time-proximity+same-text-v1',
          sharedRoutedMs: Math.round(routedMs),
          matchedTokens: match.length,
          matchedCharacters,
          medianWordDeltaMs: Math.round(medianWordDeltaMs),
          runtime: {
            consensusStartMs,
            consensusEndMs,
            parties: [
              { csrc: Number(left.csrc), segmentId: String(left.segmentId), phraseStartMs: Math.round(leftInterval.startMs), phraseEndMs: Math.round(leftInterval.endMs) },
              { csrc: Number(right.csrc), segmentId: String(right.segmentId), phraseStartMs: Math.round(rightInterval.startMs), phraseEndMs: Math.round(rightInterval.endMs) },
            ],
          },
          traceCounts: { [String(left.segmentId)]: leftTrace.traceCount, [String(right.segmentId)]: rightTrace.traceCount },
          traceHashes: { [String(left.segmentId)]: leftTrace.submissionHashes, [String(right.segmentId)]: rightTrace.submissionHashes },
        },
      });
    }
  }

  return {
    kind: AUTO_WORD_CONTEST_KIND,
    rows: [...annotations.values()],
    receipt: { config, pairsConsidered, annotations: annotations.size, rejected },
  };
}
