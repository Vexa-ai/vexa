/**
 * O-TEL-2 — the DETERMINISTIC replay harness (the gate:replay integration target).
 *
 * Self-contained + OFFLINE. Replays a SMALL golden captured-signal.v1 fixture
 * (meetings/eval/replay-fixture/session.captured-signal.jsonl) through the EXACT pipeline (the
 * REAL @vexa/gmeet-pipeline lane the live bot/desktop run) and asserts the pipeline reproduces the
 * SAME transcript STRUCTURE deterministically:
 *
 *   • same input ⇒ same output — replaying the fixture TWICE yields byte-identical confirmed
 *     segments (segmentation + attribution + timing) — the core "a stored raw signal is a
 *     reproducible offline test" guarantee;
 *   • the EXPECTED structure — three turns attributed Alice → Bob → Alice from the glow names
 *     carried on the frames (the captured signal ALONE drives segmentation, no live meeting);
 *   • every emitted segment is transcript.v1-valid (ajv vs the published SSOT schema).
 *
 * Real STT isn't in CI, so a deterministic MOCK transcribe stands in (text keyed off the frame
 * level) — the assertion is "the pipeline produces the SAME segmentation/structure for the same
 * raw signal", NOT STT text quality. This is the in-process twin of eval/src/replay.mjs (which
 * re-sends a stored signal into a LIVE desktop ingest); here we drive the lane directly so it needs
 * no server, no model, no network — exactly what gate:replay requires. It lives in the bot package
 * because that is where @vexa/gmeet-pipeline + ajv resolve; the fixture lives in meetings/eval.
 *
 * Run (the gate:replay command):  npx tsx src/replay.test.ts   (from meetings/services/bot)
 *   equivalently:  pnpm --filter @vexa/bot exec tsx src/replay.test.ts   (from the repo root)
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createGmeetPipeline, type TranscriptSegment } from '@vexa/gmeet-pipeline';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, '..', '..', '..', 'eval', 'replay-fixture', 'session.captured-signal.jsonl');
const TX_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'transcript.v1', 'transcript.schema.json');

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── transcript.v1 validator (ajv against the PUBLISHED schema; P8) ──
const txSchema = JSON.parse(readFileSync(TX_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(txSchema);
const validateSeg: ValidateFunction = ajv.compile({ $ref: `${txSchema.$id}#/$defs/TranscriptSegment` });

interface CapFrame { seq: number; ts: number; speakerIndex: number; speakerName?: string; pcm: string; pcm_len: number; lane: string; }
interface CapHeader { type: string; v: number; platform: string; native_meeting_id: string; lane: string; }

// ── Load the captured-signal.v1 fixture: a header line + N frame lines (JSONL). ──
function loadCapturedSignal(path: string): { header: CapHeader; frames: CapFrame[] } {
  const lines = readFileSync(path, 'utf8').split('\n').filter(Boolean);
  const header = JSON.parse(lines[0]) as CapHeader;
  if (header.type !== 'captured_signal_header') throw new Error('not a captured-signal.v1 fixture (bad header)');
  const frames = lines.slice(1).map((l) => JSON.parse(l) as CapFrame);
  return { header, frames };
}

// A captured frame's base64 PCM → Float32Array (the codec wire payload, decoded).
function framePcm(f: CapFrame): Float32Array {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
}

/**
 * Replay a captured-signal.v1 session through the REAL gmeet lane with a deterministic mock
 * transcribe, returning the CONFIRMED segments. No wall-clock pacing — each frame carries its own
 * capture ts, which drives the lane's segmentation, so replaying instantly stays deterministic.
 */
async function replayThroughPipeline(frames: CapFrame[]): Promise<TranscriptSegment[]> {
  const confirmed: TranscriptSegment[] = [];
  // Deterministic mock STT: text keyed off the frame energy so two speakers get distinct lines
  // (the pipeline's job here is segmentation/attribution, not STT quality).
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    const text = pcm[0] > 0.07 ? 'second speaker line' : 'first speaker line';
    return { text, language: 'en', duration: pcm.length / 16000, segments: [{ start: 0, end: pcm.length / 16000, text }] };
  };
  const pipe = createGmeetPipeline({
    transcribe,
    // Fast lane config so a turn confirms within the fixture's own ts timeline (deterministic).
    config: { minAudioDuration: 0.15, submitInterval: 0.1, confirmThreshold: 2, maxBufferDuration: 5, idleTimeoutSec: 2, sampleRate: 16000 },
    sink: { segment: (s) => { if (s.completed) confirmed.push(s); }, draft: () => { /* */ }, finalize: () => { /* */ } },
  });
  for (const f of frames) pipe.feedAudio(f.speakerIndex, f.speakerName, framePcm(f), f.ts);
  await pipe.dispose();   // flush every turn → finalize
  // Stable order for the determinism comparison (the lane may flush turns out of feed order).
  return confirmed.slice().sort((a, b) => (a.start - b.start) || (a.speaker_key ?? '').localeCompare(b.speaker_key ?? ''));
}

async function main(): Promise<void> {
  const session = loadCapturedSignal(FIXTURE);
  check('fixture is a captured-signal.v1 session (header + frames)',
    session.header.v === 1 && session.frames.length > 0, `frames=${session.frames.length}`);

  // ── Replay #1 + #2 — same input MUST yield the same output (determinism). ──
  const run1 = await replayThroughPipeline(session.frames);
  const run2 = await replayThroughPipeline(session.frames);

  check('replay produced confirmed segments', run1.length > 0, `n=${run1.length}`);
  check('every confirmed segment is transcript.v1-valid (ajv vs SSOT)',
    run1.length > 0 && run1.every((s) => !!validateSeg(s)), ajv.errorsText(validateSeg.errors));

  // DETERMINISM: byte-identical segments across the two replays (segmentation, attribution, timing).
  const norm = (segs: TranscriptSegment[]): string =>
    JSON.stringify(segs.map((s) => ({ speaker: s.speaker, speaker_key: s.speaker_key, text: s.text, start: s.start, end: s.end })));
  check('same input ⇒ same output (replay is deterministic)', norm(run1) === norm(run2),
    `\n      run1=${norm(run1)}\n      run2=${norm(run2)}`);

  // STRUCTURE: the captured signal alone drives the expected three-turn Alice → Bob → Alice shape.
  const speakers = run1.map((s) => s.speaker);
  check('attribution from the captured glow names: Alice present', speakers.includes('Alice'), speakers.join(','));
  check('attribution from the captured glow names: Bob present', speakers.includes('Bob'), speakers.join(','));
  check('three distinct turns segmented (Alice → Bob → Alice)', speakers.length === 3 &&
    speakers[0] === 'Alice' && speakers[1] === 'Bob' && speakers[2] === 'Alice', speakers.join(' → '));
  check('text follows the per-speaker signal (no cross-channel mislabel)',
    run1.filter((s) => s.speaker === 'Alice').every((s) => s.text === 'first speaker line') &&
    run1.filter((s) => s.speaker === 'Bob').every((s) => s.text === 'second speaker line'),
    JSON.stringify(run1.map((s) => `${s.speaker}:${s.text}`)));

  if (failed) { console.error(`\n❌ replay (O-TEL-2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ replay (O-TEL-2): a stored captured-signal.v1 fixture replays through the EXACT gmeet pipeline to a deterministic transcript — same input ⇒ same output, expected Alice→Bob→Alice structure, every segment transcript.v1-valid. Offline, no STT model, no server.');
}

void main();
