// signal-metrics — the metrics a recorded session yields with NO ground truth and NO pipeline run.
//
// These are the framework's *delivery* and *shape* axes (FRAMEWORK.md): they answer "did the audio
// arrive, and did the boundaries look like speech?" before anything downstream is blamed. They are
// pure functions of the `captured-signal.v1` file, so they are cheap, deterministic, and identical
// whether the session came from a bot in production or a desktop tape a human just witnessed —
// which is what lets one number (capture duty cycle) be compared across platforms at all.
//
// The one metric that has already paid for itself: duty cycle 65.0% on jitsi vs 94.9% on the
// youtube control attributed a "the segmenter over-segments" complaint to CAPTURE instead.
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';

export const SILENT_RMS = 0.002;   // below this a frame carries no speech worth transcribing
const GAP_MS = 100;                // a delivery hole worth counting (frames arrive every ~64-256ms)

export function loadSession(path) {
  const raw = path.endsWith('.gz')
    ? gunzipSync(readFileSync(path)).toString('utf8')
    : readFileSync(path, 'utf8');
  const lines = raw.split('\n').filter(Boolean);
  const header = JSON.parse(lines[0]);
  if (header.type !== 'captured_signal_header') throw new Error(`${path}: not a captured-signal.v1 session`);
  const recs = lines.slice(1).map((l) => JSON.parse(l));
  return {
    header,
    frames: recs.filter((r) => r.type !== 'hint' && r.type !== 'boundary'),
    hints: recs.filter((r) => r.type === 'hint'),
    cuts: recs.filter((r) => r.type === 'boundary'),
  };
}

const pct = (xs, p) => (xs.length ? xs.slice().sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(xs.length * p))] : 0);
const r3 = (n) => Number(n.toFixed(3));

export function sessionMetrics(session) {
  const { header, frames, hints, cuts } = session;
  const sr = header.sample_rate ?? 16000;
  const ts = frames.map((f) => f.ts).sort((a, b) => a - b);
  const wallSec = ts.length > 1 ? (ts[ts.length - 1] - ts[0]) / 1000 : 0;
  const audioSec = frames.reduce((n, f) => n + (f.pcm_len ?? 0) / sr, 0);

  // Delivery holes: wall time between one frame's END and the next frame's start. A frame covers
  // pcm_len/sr seconds of audio, so a stream that never stalls has ~zero residual here. This is the
  // measurement — the duty cycle is its summary; the gap list is where it happened.
  const ordered = frames.slice().sort((a, b) => a.ts - b.ts);
  const gaps = [];
  for (let i = 1; i < ordered.length; i++) {
    const covered = ((ordered[i - 1].pcm_len ?? 0) / sr) * 1000;
    const gap = ordered[i].ts - ordered[i - 1].ts - covered;
    if (gap > GAP_MS) gaps.push({ atSec: r3((ordered[i - 1].ts - ts[0]) / 1000), gapSec: r3(gap / 1000) });
  }
  const gapSecs = gaps.map((g) => g.gapSec);

  // Cut cadence: the segmenter's own opinion of where speech units end. Inter-cut p50 well under a
  // second means turns too short for LocalAgreement to ever confirm one.
  const cutTs = cuts.map((c) => c.tMs).sort((a, b) => a - b);
  const interCut = cutTs.slice(1).map((t, i) => (t - cutTs[i]) / 1000);

  const withRms = frames.filter((f) => typeof f.rms === 'number');
  const silentFrames = withRms.filter((f) => f.rms < SILENT_RMS).length;

  return {
    frames: frames.length,
    hints: hints.length,
    cuts: cuts.length,
    audioSec: r3(audioSec),
    wallSec: r3(wallSec),
    // THE delivery number: seconds of audio delivered per second of wall clock.
    dutyCycle: wallSec ? r3(audioSec / wallSec) : null,
    gapCount: gaps.length,
    gapTotalSec: r3(gapSecs.reduce((a, b) => a + b, 0)),
    gapP50Sec: r3(pct(gapSecs, 0.5)),
    gapMaxSec: r3(gapSecs.length ? Math.max(...gapSecs) : 0),
    interCutP50Sec: r3(pct(interCut, 0.5)),
    cutsUnder1s: interCut.filter((d) => d < 1).length,
    silentFrameRatio: withRms.length ? r3(silentFrames / withRms.length) : null,
    distinctHintNames: new Set(hints.map((h) => h.name)).size,
    worstGaps: gaps.slice().sort((a, b) => b.gapSec - a.gapSec).slice(0, 5),
  };
}

export function metricsFor(path) {
  return sessionMetrics(loadSession(path));
}

/** Human-readable one-block report — the same lines whatever produced the session. */
export function formatMetrics(m) {
  return [
    `  frames ${m.frames} · hints ${m.hints} (${m.distinctHintNames} names) · recorded cuts ${m.cuts}`,
    `  delivery  duty cycle ${m.dutyCycle === null ? '—' : (m.dutyCycle * 100).toFixed(1) + '%'}` +
      `  (${m.audioSec.toFixed(1)}s audio over ${m.wallSec.toFixed(1)}s wall)`,
    `            gaps >${GAP_MS}ms: ${m.gapCount} totalling ${m.gapTotalSec.toFixed(1)}s · p50 ${m.gapP50Sec.toFixed(2)}s · max ${m.gapMaxSec.toFixed(2)}s`,
    `  shape     inter-cut p50 ${m.interCutP50Sec.toFixed(2)}s · ${m.cutsUnder1s} cuts under 1s` +
      `${m.silentFrameRatio === null ? '' : ` · silent frames ${(m.silentFrameRatio * 100).toFixed(1)}%`}`,
  ].join('\n');
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const p = process.argv[2];
  if (!p) { console.error('usage: signal-metrics.mjs <session.captured-signal.jsonl[.gz]>'); process.exit(1); }
  const m = metricsFor(p);
  console.log(p);
  console.log(formatMetrics(m));
}
