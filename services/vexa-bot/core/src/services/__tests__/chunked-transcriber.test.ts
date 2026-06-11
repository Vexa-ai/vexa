/**
 * ChunkedTranscriber unit tests — drives the cut/merge/queue/attribution
 * logic directly via handleCommit (no ONNX models, fake Whisper).
 *
 * Run: npx tsx src/services/__tests__/chunked-transcriber.test.ts
 */

import { ChunkedTranscriber, ChunkSegment } from '../chunked-transcriber';
import { TranscriptionResult } from '../transcription-client';

const SR = 16000;
let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail = '') {
  if (cond) { passed++; console.log(`  PASS  ${name}`); }
  else { failed++; console.log(`  FAIL  ${name} ${detail}`); }
}

/** Tone frame (non-silent) of `ms` at wall-time tMs. */
function tone(ms: number): Float32Array {
  const n = Math.round((ms / 1000) * SR);
  const a = new Float32Array(n);
  for (let i = 0; i < n; i++) a[i] = 0.1 * Math.sin(i / 10);
  return a;
}

function silence(ms: number): Float32Array {
  return new Float32Array(Math.round((ms / 1000) * SR));
}

interface Call { samples: number; prompt?: string }

async function makeT(opts?: { text?: (call: number) => string }) {
  const calls: Call[] = [];
  const published: Array<{ speaker: string; segments: ChunkSegment[] }> = [];
  const renamed: Array<{ from: string; to: string; segments: ChunkSegment[] }> = [];
  let n = 0;
  const t: any = Object.create(ChunkedTranscriber.prototype);
  // Mirror the private constructor's state (create() needs ONNX models).
  Object.assign(t, {
    cb: {
      language: 'en',
      transcribe: async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
        calls.push({ samples: pcm.length, prompt });
        const text = opts?.text ? opts.text(n++) : `so we kept talking about point number ${n++} for a while`;
        return { text, language: 'en', duration: pcm.length / SR, segments: [{ text, start: 0, end: pcm.length / SR }] };
      },
      publish: (speaker: string, segments: ChunkSegment[]) => published.push({ speaker, segments }),
      rename: (from: string, to: string, segments: ChunkSegment[]) => renamed.push({ from, to, segments }),
    },
    log: () => {},
    binder: new (await import('../cluster-name-binder')).ClusterNameBinder({}),
    diarizer: null,
    ring: [], ringMs: 0, carry: null, queue: [], pumping: false,
    lastEmittedText: '', chunkCounter: 0, disposed: false, unresolved: [],
  });
  return { t, calls, published, renamed };
}

function feed(t: any, tMs: number, pcm: Float32Array) {
  t.feedAudio(pcm, tMs);
}

async function drain() {
  await new Promise(r => setTimeout(r, 10));
}

(async () => {
  // 1. basic cut: commit ≥ MIN_CHUNK → one transcribe with exact samples
  {
    const { t, calls, published } = await makeT();
    feed(t, 0, tone(3000));
    t.recordHint('Alice', 'dom-active', 100);
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('cut: one whisper call per commit', calls.length === 1);
    check('cut: exact span samples', calls[0]?.samples === 2 * SR, `got ${calls[0]?.samples}`);
    check('cut: published once', published.length === 1);
    check('cut: hint-resolved speaker', published[0]?.speaker === 'Alice', published[0]?.speaker);
    check('cut: stable segment id', published[0]?.segments[0]?.segmentId === 'mix:0:0');
  }

  // 2. short commit carries, merges into next contiguous commit
  {
    const { t, calls } = await makeT();
    feed(t, 0, tone(4000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 400 });       // < 700 → carry
    await drain();
    check('merge: short span not transcribed alone', calls.length === 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 500, tEndMs: 2500 });     // gap 100ms → merge
    await drain();
    check('merge: merged into one call', calls.length === 1);
    check('merge: span covers both', calls[0]?.samples === Math.round(2.5 * SR), `got ${calls[0]?.samples}`);
  }

  // 3. RMS gate: silent chunk never reaches Whisper
  {
    const { t, calls } = await makeT();
    feed(t, 0, silence(3000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('gate: silence dropped before whisper', calls.length === 0);
  }

  // 4. prompt chaining: chunk N+1 gets chunk N's text
  {
    const { t, calls } = await makeT();
    feed(t, 0, tone(6000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 1500 });
    t.handleCommit({ speakerId: 's0', tStartMs: 1500, tEndMs: 3000 });
    await drain();
    check('prompt: first chunk has none', calls[0]?.prompt === undefined);
    check('prompt: second chunk chained', calls[1]?.prompt === 'so we kept talking about point number 0 for a while', calls[1]?.prompt);
  }

  // 5. no hints → provisional cluster id; late hint renames same segments
  {
    const { t, published, renamed } = await makeT();
    feed(t, 0, tone(3000));
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('late: provisional id published', published[0]?.speaker === 'speaker_0', published[0]?.speaker);
    t.recordHint('Bob', 'dom-active', 500);
    check('late: renamed on hint', renamed.length === 1 && renamed[0].to === 'Bob');
    check('late: same segment ids', renamed[0]?.segments[0]?.segmentId === published[0]?.segments[0]?.segmentId);
  }

  // 6. serialization: queued chunks transcribe strictly in order
  {
    const order: number[] = [];
    const { t } = await makeT();
    let active = 0; let overlapped = false;
    (t as any).cb.transcribe = async (pcm: Float32Array) => {
      active++; if (active > 1) overlapped = true;
      await new Promise(r => setTimeout(r, 5));
      order.push(pcm.length); active--;
      return { text: 'x', language: 'en', duration: 1, segments: [{ text: 'x', start: 0, end: 1 }] };
    };
    feed(t, 0, tone(8000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 1000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 1000, tEndMs: 3000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 3000, tEndMs: 4000 });
    await new Promise(r => setTimeout(r, 80));
    check('serial: never concurrent', !overlapped);
    check('serial: FIFO order', order[0] === SR && order[1] === 2 * SR && order[2] === SR, JSON.stringify(order));
  }

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
