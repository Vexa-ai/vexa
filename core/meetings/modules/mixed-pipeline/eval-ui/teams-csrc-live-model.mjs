import { detectContestedWords } from './teams-contested-word-detector.mjs';

const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

export function flagContestedText(text, contestedText) {
  const source = String(text ?? '');
  const needle = String(contestedText ?? '');
  const start = source.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  if (!needle || start < 0) return source;
  const end = start + needle.length;
  return `${source.slice(0, start)}[${source.slice(start, end)}]${source.slice(end)}`;
}

export function buildContestPlan(result) {
  const detected = detectContestedWords(result);
  const firstConfirmedAt = new Map();
  for (const event of result.candidate?.events ?? []) {
    if (!event.completed || !event.text?.trim()) continue;
    const id = String(event.segmentId);
    const previous = firstConfirmedAt.get(id);
    if (previous === undefined || event.emittedAtMs < previous) firstConfirmedAt.set(id, event.emittedAtMs);
  }
  return detected.rows.map((contest, index) => {
    const leftAt = firstConfirmedAt.get(String(contest.segmentId));
    const rightAt = firstConfirmedAt.get(String(contest.rivalSegmentId));
    return {
      ...contest,
      index,
      decisionAtMs: Number.isFinite(leftAt) && Number.isFinite(rightAt)
        ? Math.max(leftAt, rightAt)
        : Number.POSITIVE_INFINITY,
    };
  });
}

export function toTranscriptSegment(event, cutStartMs, contest = null) {
  const text = contest
    ? flagContestedText(event.text, contest.contestedText)
    : String(event.text ?? '');
  const latencyMs = finite(event.emittedAtMs) - finite(event.startMs);
  return {
    ...event,
    text,
    // The actual Teams candidate earns this through TrackNamer. Static historical results predate
    // that field, so their diagnostic CSRC label remains an explicit fallback rather than a guess.
    speaker: event.speaker || `CSRC ${event.csrc}`,
    segment_id: String(event.segmentId),
    start_time: Math.max(0, (finite(event.startMs) - cutStartMs) / 1000),
    end_time: Math.max(0, (finite(event.endMs) - cutStartMs) / 1000),
    absolute_start_time: new Date(finite(event.startMs)).toISOString(),
    absolute_end_time: new Date(Math.max(finite(event.startMs), finite(event.endMs))).toISOString(),
    updated_at: new Date(finite(event.emittedAtMs)).toISOString(),
    latencyMs,
    contested: Boolean(contest),
  };
}
