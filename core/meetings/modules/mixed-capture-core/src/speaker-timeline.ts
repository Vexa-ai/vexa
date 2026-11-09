/**
 * createSpeakerTimeline — who was speaking at a given capture time, from a platform's speaking edges.
 * ONE implementation for every host that names a single mixed stream (card brain:card/66d7eaa642e5):
 * Teams' PageCapture (its server mix) and the capture extension's named fallback (a tab's audio, named by
 * Zoom's or Meet's active-speaker tile). Rules: keyed by tile id (two tiles with one display name are two
 * speakers); a name only when exactly ONE named participant was speaking at that time and no unnamed one was
 * (never a guess); edges kept for `keepMs`.
 */
export interface SpeakerTimeline {
  /** A speaking edge: `id` keys the participant (falls back to the name), tMs is epoch ms. */
  speak(id: string, name: string, tMs: number, isEnd: boolean): void;
  /** A participant whose name did not resolve started or stopped speaking. */
  unnamed(edge: 'start' | 'end', tMs: number): void;
  /** The one named speaker at `ts`, or null. */
  nameAt(ts: number): string | null;
  /** Edges seen so far (diagnostics). */
  edges(): number;
}

export function createSpeakerTimeline(opts: { keepMs?: number } = {}): SpeakerTimeline {
  const keepMs = opts.keepMs ?? 60_000;
  const spans: Array<{ id: string; name: string; start: number; end: number }> = [];
  const unnamedSpans: Array<{ start: number; end: number }> = [];
  let edges = 0;
  const prune = (now: number): void => {
    for (const list of [spans, unnamedSpans] as Array<Array<{ end: number }>>) {
      for (let i = list.length - 1; i >= 0; i--) if (list[i].end < now - keepMs) list.splice(i, 1);
    }
  };
  return {
    speak(id, name, tMs, isEnd) {
      edges++;
      const key = id || name;
      if (!isEnd) {
        if (!spans.some((s) => s.id === key && s.end === Infinity)) spans.push({ id: key, name, start: tMs, end: Infinity });
      } else {
        const open = spans.find((s) => s.id === key && s.end === Infinity);
        if (open) open.end = tMs;
      }
      prune(tMs);
    },
    unnamed(edge, tMs) {
      edges++;
      if (edge === 'start') unnamedSpans.push({ start: tMs, end: Infinity });
      else { const open = unnamedSpans.find((u) => u.end === Infinity); if (open) open.end = tMs; }
    },
    nameAt(ts) {
      if (unnamedSpans.some((u) => u.start <= ts && ts < u.end)) return null;
      const live = spans.filter((s) => s.start <= ts && ts < s.end);
      const ids = new Set(live.map((s) => s.id));
      return ids.size === 1 ? live[0].name : null;
    },
    edges: () => edges,
  };
}
