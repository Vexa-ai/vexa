/**
 * O-TEL-2 (mixed lane) — a recorded Teams/Zoom/Jitsi session replays to the SAME ATTRIBUTION.
 *
 * The gmeet twin (replay.test.ts) proves segmentation off per-channel, glow-named frames. The
 * mixed lane is where the attribution bugs live (#797 · #499 · #539): ONE audio stream carrying
 * everybody, named only by out-of-band speaker hints. So the question this harness answers is not
 * "did the audio survive" but "does WHO SPOKE reproduce from the stored signal alone".
 *
 * The fixture is REAL harvested signal — a live jitsi meeting recorded through the branch's
 * capture-signal recorder, distilled to the ~7s window around one Anna→Boris turn change
 * (eval/replay-fixture/session-mixed.captured-signal.jsonl: 18 audio frames + 4 hint records).
 * Two scripted speakers whose microphones were ground-truth WAVs produced it, so the expected
 * attribution is known independently of anything the pipeline says.
 *
 * Determinism comes from replacing the two non-deterministic dependencies, and ONLY those:
 *   • STT      → a mock keyed off the frame clock (the assertion is attribution, never ASR quality);
 *   • the cut  → an injected BoundarySource (production's PyannoteSegmenter is a model download).
 * Everything between them is the REAL @vexa/mixed-pipeline the live bot runs.
 *
 * Run: npx tsx src/replay-mixed.test.ts   (REPLAY_MIXED_FIXTURE=<file> to point at any session)
 */
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ChunkedTranscriber, type BoundaryEvent, type BoundarySource } from '@vexa/mixed-pipeline';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = process.env.REPLAY_MIXED_FIXTURE
  ?? join(HERE, '..', '..', '..', 'eval', 'replay-fixture', 'session-mixed.captured-signal.jsonl.gz');

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface Frame { seq: number; ts: number; pcm: string; pcm_len: number; lane: string; }
interface Hint { type: 'hint'; t: number; name: string; isEnd?: boolean; }

function load(path: string): { header: any; frames: Frame[]; hints: Hint[]; cuts: Cut[] } {
  // Sessions are PCM-heavy; the committed fixture is stored gzipped (27 KB vs 1.2 MB).
  const raw = path.endsWith('.gz') ? gunzipSync(readFileSync(path)).toString('utf8') : readFileSync(path, 'utf8');
  const lines = raw.split('\n').filter(Boolean);
  const header = JSON.parse(lines[0]);
  if (header.type !== 'captured_signal_header') throw new Error('not a captured-signal.v1 session');
  const recs = lines.slice(1).map((l) => JSON.parse(l));
  return {
    header,
    frames: recs.filter((r: any) => r.type !== 'hint' && r.type !== 'boundary') as Frame[],
    hints: recs.filter((r: any) => r.type === 'hint') as Hint[],
    cuts: recs.filter((r: any) => r.type === 'boundary') as Cut[],
  };
}

const framePcm = (f: Frame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
};

/** Replay one recorded mixed session: audio + hints in their recorded ORDER and clock, cutting a
 *  turn wherever the recording says the active speaker changed. Returns [speaker, text] pairs. */
async function replay(frames: Frame[], hints: Hint[], cuts: Cut[] = []): Promise<Array<[string, string]>> {
  const published: Array<[string, string]> = [];
  let emitBoundary!: (ev: BoundaryEvent) => void;

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    // Deterministic stand-in for Whisper: the text names the audio-time window it came from, so a
    // segment that lands under the wrong speaker is visible as a mismatch, not as bad ASR.
    transcribe: async (pcm: Float32Array): Promise<TranscriptionResult> => {
      const dur = pcm.length / 16000;
      const text = `speech(${dur.toFixed(1)}s)`;
      return { text, language: 'en', duration: dur, segments: [{ start: 0, end: dur, text }] } as TranscriptionResult;
    },
    publish: (speaker, confirmed) => { for (const c of confirmed) published.push([speaker, c.text]); },
    publishPending: () => { /* drafts are not the oracle */ },
    clearPending: () => { /* */ },
    rename: (oldSpeaker, newSpeaker) => {
      // A late hint renaming a provisional turn is a CORRECT outcome, not a failure — record it.
      for (const p of published) if (p[0] === oldSpeaker) p[0] = newSpeaker;
    },
    makeSegmenter: (onBoundary) => {
      emitBoundary = onBoundary;
      return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } });
    },
    log: () => { /* quiet */ },
  });

  // Interleave audio, hints and cuts on ONE timeline, exactly as recorded.
  type Ev = { t: number; frame?: Frame; hint?: Hint; cut?: Cut };
  const timeline: Ev[] = [
    ...frames.map((f) => ({ t: f.ts, frame: f })),
    ...hints.map((h) => ({ t: h.t, hint: h })),
    ...cuts.map((c) => ({ t: c.tMs, cut: c })),
  ].sort((a, b) => a.t - b.t);
  // A session recorded with production's own cuts replays with THOSE; only a session that
  // predates cut-recording falls back to the substitute below (speaker-change only), which
  // chunks differently from production and must not be mistaken for it.
  const useRecordedCuts = cuts.length > 0;

  // ONE clock for audio, hints and cuts — the capture bridge's contract is that hint tMs and
  // audio tsMs share the epoch-ms domain (capture-bridge.ts: HINT_MAX_SKEW_MS guards exactly
  // this). Replaying audio on a relative clock while hints keep their epoch stamps puts every
  // hint ~56 years past every turn, and the binder matches nothing.
  let current = '';
  emitBoundary({ kind: 'silence→speaker', tMs: timeline[0].t, confidence: 0.9 });
  for (const ev of timeline) {
    if (ev.cut) {
      emitBoundary({ kind: ev.cut.kind as BoundaryEvent['kind'], tMs: ev.cut.tMs, confidence: ev.cut.confidence ?? 0.9 });
    } else if (ev.hint) {
      const h = ev.hint;
      // Substitute cut source: only when the session carries no recorded ones.
      if (!useRecordedCuts && !h.isEnd && h.name !== current) {
        if (current) emitBoundary({ kind: 'speaker→speaker', tMs: h.t, confidence: 0.9 });
        current = h.name;
      }
      tc.recordHint(h.name, 'dom-active', h.t, h.isEnd);
    } else if (ev.frame) {
      tc.feedAudio(framePcm(ev.frame), ev.frame.ts);
    }
  }
  emitBoundary({ kind: 'speaker→silence', tMs: timeline[timeline.length - 1].t, confidence: 0.9 });
  await sleep(1500);          // let the confirm loop drain
  await tc.dispose();          // flush the open turn
  return published;
}

async function main(): Promise<void> {
  const { header, frames, hints, cuts } = load(FIXTURE);
  console.log(`  fixture: ${frames.length} frames + ${hints.length} hints + ${cuts.length} recorded cuts (${header.platform}/${header.lane})`);
  console.log(cuts.length
    ? '  chunking: production\'s OWN recorded cuts'
    : '  chunking: SUBSTITUTE cut source (speaker-change only) — this session predates cut recording');

  check('fixture is a MIXED-lane captured-signal.v1 session', header.lane === 'mixed', header.lane);
  check('the session carries the out-of-band hints attribution needs', hints.length > 0,
    'no hint records — a mixed session without hints can only ever replay as anonymous audio');

  const run1 = await replay(frames, hints, cuts);
  const run2 = await replay(frames, hints, cuts);

  const norm = (r: Array<[string, string]>) => JSON.stringify(r);
  check('replay produced attributed segments', run1.length > 0, `n=${run1.length}`);
  check('same input ⇒ same output (mixed replay is deterministic)', norm(run1) === norm(run2),
    `\n      run1=${norm(run1)}\n      run2=${norm(run2)}`);

  const speakers = [...new Set(run1.map(([s]) => s))].sort();
  const hintNames = [...new Set(hints.map((h) => h.name))].sort();
  // Both attribution checks are guarded on a NON-EMPTY replay: "every segment is named" is
  // trivially true of no segments, and a fixture that silently produces nothing must fail loud
  // rather than green three rows vacuously.
  check('every attributed name came from a recorded hint (no invented speakers)',
    run1.length > 0 && speakers.every((s) => hintNames.includes(s)),
    `got ${speakers.join(',') || '(nothing)'} / hints ${hintNames.join(',')}`);
  check('no segment is left anonymous (seg_N / empty)',
    run1.length > 0 && run1.every(([s]) => s && !/^seg[_-]?\d+$/i.test(s)),
    JSON.stringify(run1.map(([s]) => s)));

  // The fixture spans ONE recorded turn change, so both ground-truth speakers must appear —
  // this is the row that goes red for the #797/#499/#539 class (audio survives, attribution
  // collapses to a single name or to seg_N).
  if (!process.env.REPLAY_MIXED_FIXTURE) {
    check('both recorded speakers are attributed across the turn change',
      speakers.length === 2 && speakers.join(',') === 'Anna,Boris', speakers.join(','));
  }

  console.log(`  attributed: ${run1.map(([s, t]) => `${s}:${t}`).join('  ')}`);
  if (failed) { console.error(`\n❌ replay-mixed (O-TEL-2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ replay-mixed (O-TEL-2): a recorded MIXED-lane session replays through the real @vexa/mixed-pipeline to deterministic, hint-derived speaker attribution — offline, no model, no meeting.');
}

void main();
