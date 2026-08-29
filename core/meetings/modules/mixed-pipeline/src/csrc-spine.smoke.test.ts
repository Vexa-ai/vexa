/**
 * The transport spine, end to end through the REAL ChunkedTranscriber.
 *
 * The unit tests prove the spine cuts correctly and the namer refuses to guess. This one proves the
 * two of them together produce a TRANSCRIPT with the properties the lane exists for:
 *
 *   1. a turn on a named track publishes under the human's name;
 *   2. a name that arrives LATE repaints what the track already published, in place;
 *   3. crosstalk publishes as "Speaker" — never as either participant;
 *   4. 'auto' does not open a hole: pyannote carries the session until the transport speaks, and
 *      a session where the transport NEVER speaks is exactly today's lane plus one observation.
 *
 * Run: npx tsx src/csrc-spine.smoke.test.ts
 */
import { ChunkedTranscriber, type BoundarySource, type ChunkSegment, type TurnSourceObservation } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

/** Audio carrying a decodable VOICE. Real speakers produce different words; a stub that returned
 *  the same marker text for every turn would be filtered by the lane's own prompt-echo guard (each
 *  turn's text would be a substring of the previous turn's prompt), and the test would be measuring
 *  the stub. The voice is encoded in the amplitude and decoded by the stub below. */
const AMP = (voice: number): number => 0.15 + voice * 0.08;
const speech = (ms: number, voice: number): Float32Array => {
  const a = new Float32Array(Math.round(16 * ms));
  for (let i = 0; i < a.length; i++) a[i] = Math.sin(i / 7) * AMP(voice);
  return a;
};
const voiceOf = (pcm: Float32Array): number => {
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  const amp = Math.sqrt(sum / Math.max(1, pcm.length)) * Math.SQRT2;
  return Math.max(0, Math.round((amp - 0.15) / 0.08));
};

interface Row { speaker: string; text: string; id: string }

async function run(opts: {
  turnSource: 'csrc' | 'auto';
  graceMs?: number;
  script: (api: {
    audio: (durMs: number, voice?: number) => Promise<void>;
    csrc: (id: number, active: boolean) => void;
    hint: (name: string) => void;
    now: () => number;
  }) => Promise<void>;
}): Promise<{ rows: Row[]; renames: Array<{ from: string; to: string }>; observations: TurnSourceObservation[]; stats: any }> {
  const durable = new Map<string, Row>();
  const renames: Array<{ from: string; to: string }> = [];
  const observations: TurnSourceObservation[] = [];
  let boundary!: (ev: BoundaryEvent) => void;
  let word = 0;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    turnSource: opts.turnSource,
    turnSourceGraceMs: opts.graceMs,
    // A deterministic stand-in for Whisper: one marker word per second, growing by a STABLE PREFIX
    // so LocalAgreement can actually confirm. Text is not what this test is about; who it lands
    // under is.
    transcribe: async (pcm) => {
      const secs = Math.max(1, Math.round(pcm.length / 16000));
      const v = voiceOf(pcm);
      const text = Array.from({ length: secs }, (_, i) => `v${v}w${i}`).join(' ');
      word++;
      return {
        text, language: 'en', language_probability: 0.99, duration: pcm.length / 16000,
        segments: [{ text, start: 0, end: pcm.length / 16000, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
      };
    },
    publish: (speaker, confirmed) => { for (const c of confirmed) durable.set(c.segmentId, { speaker, text: c.text, id: c.segmentId }); },
    publishPending: () => { /* drafts are not the subject here */ },
    clearPending: () => { /* ditto */ },
    rename: (from, to, segs: ChunkSegment[]) => {
      renames.push({ from, to });
      for (const s of segs) durable.set(s.segmentId, { speaker: to, text: s.text, id: s.segmentId });
    },
    onObservation: (o) => observations.push(o),
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      boundary = onBoundary;
      return { appendFrame: async () => { /* boundaries are driven by the script */ }, reset: () => { /* n/a */ } };
    },
    log: () => { /* quiet */ },
  });

  let t = 1_700_000_000_000;
  const api = {
    async audio(durMs: number, voice = 0): Promise<void> {
      // 100ms frames, so the spine's clock and the namer's horizon advance the way they do live.
      for (let i = 0; i < durMs; i += 100) { tc.feedAudio(speech(100, voice), t); t += 100; }
      await new Promise((r) => setImmediate(r));
    },
    csrc: (id: number, active: boolean) => tc.recordTransportEvent({ csrc: id, active, tMs: t }),
    hint: (name: string) => tc.recordHint(name, 'dom-outline', t),
    now: () => t,
  };
  void boundary; void word;
  await opts.script(api);
  await tc.dispose();
  return { rows: [...durable.values()], renames, observations, stats: tc.stats() };
}

// ── 1) A named track publishes under the human's name; a LATE name repaints in place ─────────────
{
  const { rows, renames, stats } = await run({
    turnSource: 'csrc',
    script: async ({ audio, csrc, hint }) => {
      // Ana speaks three times. Her tile lights (with the ~1s lag the fixtures measured) only from
      // the SECOND run on — so the first run publishes before anyone knows who she is.
      csrc(1, true); await audio(3000, 1); csrc(1, false); await audio(1500, 0);
      csrc(1, true); hint('Ana'); await audio(1000, 2); hint('Ana'); await audio(2500, 2); csrc(1, false); await audio(1500, 0);
      csrc(1, true); hint('Ana'); await audio(1000, 3); hint('Ana'); await audio(2500, 3); csrc(1, false); await audio(4000, 0);
    },
  });
  const speakers = new Set(rows.map((r) => r.speaker));
  check('the track ended up named from its own evidence',
    stats.tracks.named === 1 && speakers.has('Ana'), `${JSON.stringify([...speakers])} ${JSON.stringify(stats.tracks)}`);
  check('the early rows were REPAINTED, not left behind under a letter',
    renames.length > 0 && !speakers.has('Speaker A'), `renames=${JSON.stringify(renames)} speakers=${JSON.stringify([...speakers])}`);
  check('every row belongs to Ana — one track, one name, no stragglers',
    rows.length > 0 && rows.every((r) => r.speaker === 'Ana'), JSON.stringify(rows.map((r) => r.speaker)));
}

// ── 2) Crosstalk is nobody's ────────────────────────────────────────────────────────────────────
{
  const { rows, stats } = await run({
    turnSource: 'csrc',
    script: async ({ audio, csrc, hint }) => {
      csrc(1, true); hint('Ana'); await audio(1500, 1); hint('Ana'); await audio(2000, 1);
      csrc(2, true); await audio(3000, 3);       // both talking — this span is unsplittable
      csrc(2, false); await audio(2500, 1);
      csrc(1, false); await audio(2000, 0);
      csrc(1, true); hint('Ana'); await audio(1500, 1); hint('Ana'); await audio(2000, 1); csrc(1, false); await audio(3000, 0);
    },
  });
  check('the contested span exists as its own turn', stats.contested >= 1, JSON.stringify(stats));
  // At this layer an unattributable turn publishes its segmentation id; the host renders that as
  // "Speaker" (pipeline.ts chunkToBotSegment). Either way it names nobody, which is the point.
  const contested = rows.filter((r) => /^seg_\d+$/.test(r.speaker) || r.speaker === 'Speaker');
  check('crosstalk published unattributed — never handed to one of the two voices in it',
    contested.length > 0, JSON.stringify(rows.map((r) => r.speaker)));
  check('no row from the crosstalk carries Ana',
    contested.every((r) => r.speaker !== 'Ana'), JSON.stringify(rows.map((r) => r.speaker)));
}

// ── 3) 'auto' with a transport that never speaks = today's lane, plus one observation ────────────
{
  const { observations, stats } = await run({
    turnSource: 'auto',
    graceMs: 3000,
    script: async ({ audio }) => { await audio(6000, 1); },
  });
  check('a session with no transport signal falls back exactly once, loudly',
    observations.filter((o) => o.reason === 'no-transport-signal').length === 1, JSON.stringify(observations));
  check('and it stays on the spine that was carrying it all along', stats.spine === 'pyannote', stats.spine);
}

// ── 4) 'auto' hands over the moment the transport speaks ─────────────────────────────────────────
{
  const { observations, stats } = await run({
    turnSource: 'auto',
    graceMs: 30_000,
    script: async ({ audio, csrc, hint }) => {
      await audio(1000, 1);                         // pyannote carries the opening — no hole
      csrc(5, true); hint('Bo'); await audio(2000, 1); hint('Bo'); await audio(2000, 1); csrc(5, false); await audio(1000, 0);
    },
  });
  check('the first transition promotes the transport to the spine',
    observations.some((o) => o.type === 'turn-source-armed' && o.to === 'csrc'), JSON.stringify(observations));
  check('and the lane is running on it', stats.spine === 'csrc', stats.spine);
  check('the transport was actually consulted (transitions counted)',
    (stats.sources.csrc?.transitions ?? 0) === 2, JSON.stringify(stats.sources));
}

if (failed) { console.error(`\n❌ csrc-spine: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ csrc-spine: transport turns publish under the track\'s earned name (repainting what came before it), crosstalk stays unattributed, and a silent transport costs one observation and nothing else.');
