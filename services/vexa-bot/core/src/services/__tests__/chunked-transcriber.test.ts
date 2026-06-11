/**
 * ChunkedTranscriber unit tests — drives the cut/turn/LocalAgreement logic
 * directly via handleCommit (no ONNX models, fake Whisper).
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
type Fake = (pcm: Float32Array, call: number) => TranscriptionResult;

/** Default fake: a stable leading sentence + a changing tail — the exact
 *  shape LocalAgreement-2 needs to confirm the head and hold the tail. */
function stableHeadFake(pcm: Float32Array, n: number): TranscriptionResult {
  const span = pcm.length / SR;
  const segs = span >= 3.5
    ? [
      { text: 'we finished the quarterly numbers today.', start: 0, end: 2 },
      { text: `and the next part keeps forming ${n}`, start: 2, end: span },
    ]
    : [{ text: `and the next part keeps forming ${n}`, start: 0, end: span }];
  return { text: segs.map(s => s.text).join(' '), language: 'en', duration: span, segments: segs };
}

async function makeT(fake?: Fake) {
  const calls: Call[] = [];
  const confirmed: Array<{ speaker: string; segments: ChunkSegment[]; tailLen: number }> = [];
  const pending: Array<{ speaker: string; segments: ChunkSegment[] }> = [];
  const cleared: string[] = [];
  const renamed: Array<{ from: string; to: string; segments: ChunkSegment[] }> = [];
  let n = 0;
  const t: any = Object.create(ChunkedTranscriber.prototype);
  // Mirror the private constructor's state (create() needs ONNX models).
  Object.assign(t, {
    cb: {
      language: 'en',
      transcribe: async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
        calls.push({ samples: pcm.length, prompt });
        return (fake || stableHeadFake)(pcm, n++);
      },
      publish: (speaker: string, segments: ChunkSegment[], tail: ChunkSegment[]) => {
        confirmed.push({ speaker, segments: [...segments], tailLen: tail.length });
        if (tail.length > 0) pending.push({ speaker, segments: [...tail] });
      },
      publishPending: (speaker: string, segments: ChunkSegment[]) => pending.push({ speaker, segments: [...segments] }),
      clearPending: (speaker: string) => cleared.push(speaker),
      rename: (from: string, to: string, segments: ChunkSegment[]) => renamed.push({ from, to, segments }),
    },
    log: () => {},
    binder: new (await import('../cluster-name-binder')).ClusterNameBinder({}),
    diarizer: null,
    ring: [], ringMs: 0, carry: null, queue: [], pumping: false,
    lastConfirmedText: '', commitCounter: 0, turnCounter: 0, disposed: false,
    turn: null, lastChunkWallMs: 0, idleTimer: null, turnRecords: [],
    latestAudioMs: 0, confirmedHighWaterMs: 0,
  });
  return { t, calls, confirmed, pending, cleared, renamed };
}

async function drain() {
  await new Promise(r => setTimeout(r, 15));
}

(async () => {
  // 1. first submission: nothing stable yet → pending only (live-edge window)
  {
    const { t, calls, confirmed, pending } = await makeT();
    t.feedAudio(tone(5000), 0);
    t.recordHint('Alice', 'dom-active', 100);
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 4000 });
    await drain();
    check('first: one whisper call to the live edge', calls.length === 1 && calls[0].samples === 5 * SR, `${calls[0]?.samples}`);
    check('first: pending only', pending.length === 1 && confirmed.length === 0);
    check('first: pending under hint name', pending[0]?.speaker === 'Alice');
  }

  // 2. second submission with stable head → head CONFIRMS mid-turn, tail pending
  {
    const { t, calls, confirmed, pending } = await makeT();
    t.feedAudio(tone(5000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });
    t.feedAudio(tone(4000), 5000);
    t.handleCommit({ speakerId: 's0', tStartMs: 4200, tEndMs: 8000 });
    await drain();
    check('confirm: head confirmed mid-turn', confirmed.length === 1, `${confirmed.length}`);
    check('confirm: sentence text', confirmed[0]?.segments[0]?.text === 'we finished the quarterly numbers today.');
    check('confirm: turn-scoped id', confirmed[0]?.segments[0]?.segmentId === 'turn:0:0');
    check('confirm: tail still pending', pending[pending.length - 1]?.segments.some(s => s.text.includes('keeps forming')));
    // window advanced: next submission starts at confirmedUpTo (2s), not 0
    t.feedAudio(tone(2000), 9000);
    t.handleCommit({ speakerId: 's0', tStartMs: 8200, tEndMs: 10000 });
    await drain();
    check('confirm: window advanced past confirmed audio', calls[2].samples === 9 * SR, `${calls[2]?.samples}`);
  }

  // 3. cluster change closes the turn → closing pass confirms everything
  {
    const { t, confirmed, cleared } = await makeT();
    t.feedAudio(tone(12000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });
    t.handleCommit({ speakerId: 's1', tStartMs: 4200, tEndMs: 8000 });
    await drain();
    const s0segs = confirmed.filter(c => c.speaker === 's0').flatMap(c => c.segments);
    check('close: everything confirmed on cluster change', s0segs.length === 2, `${s0segs.length}`);
    // pending clears via the ATOMIC close publish (confirmed + empty tail in
    // one bundle) — a separate clearPending would race the client's view.
    const lastS0 = [...confirmed].reverse().find(c => c.speaker === 's0');
    check('close: closing bundle carries empty tail', !!lastS0 && lastS0.tailLen === 0);
  }

  // 4. RMS gate: silent window never reaches Whisper
  {
    const { t, calls } = await makeT();
    t.feedAudio(silence(4000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('gate: silence dropped before whisper', calls.length === 0);
  }

  // 5. closing pass empty → pending tail promoted, never lose the turn
  {
    const { t, confirmed } = await makeT((pcm, n) =>
      n === 1
        ? { text: '', language: 'en', duration: 0, segments: [] }
        : stableHeadFake(pcm, n));
    t.feedAudio(tone(12000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });   // pending tail
    t.handleCommit({ speakerId: 's1', tStartMs: 4200, tEndMs: 8000 }); // close → empty pass
    await drain();
    const s0 = confirmed.find(c => c.speaker === 's0');
    check('promote: drafts published on empty closing pass', !!s0 && s0.segments.length > 0);
  }

  // 6. late hint renames a closed provisional turn, same ids
  {
    const { t, confirmed, renamed } = await makeT();
    t.feedAudio(tone(12000), 0);
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 4000 });
    t.handleCommit({ speakerId: 'speaker_1', tStartMs: 4200, tEndMs: 8000 });
    await drain();
    check('late: provisional cluster name used', confirmed[0]?.speaker === 'speaker_0');
    t.recordHint('Bob', 'dom-active', 500);
    check('late: renamed on hint', renamed.length === 1 && renamed[0].to === 'Bob');
    check('late: same ids', renamed[0]?.segments[0]?.segmentId === confirmed[0]?.segments[0]?.segmentId);
  }

  // 7. prompt chains from confirmed text
  {
    const { t, calls } = await makeT();
    t.feedAudio(tone(5000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });
    t.feedAudio(tone(4000), 5000);
    t.handleCommit({ speakerId: 's0', tStartMs: 4200, tEndMs: 8000 });  // confirms head
    await drain();
    t.feedAudio(tone(3000), 9000);
    t.handleCommit({ speakerId: 's0', tStartMs: 8200, tEndMs: 12000 });
    await drain();
    check('prompt: first has none', calls[0]?.prompt === undefined);
    check('prompt: chained after confirm', !!calls[2]?.prompt && calls[2].prompt!.includes('quarterly numbers'), calls[2]?.prompt);
  }

  // 8. overlap-duplicated commit never re-confirms audio (high-water clamp)
  {
    const { t, confirmed } = await makeT();
    t.feedAudio(tone(12000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });
    // diarizer overlap duplication: incoming cluster gets a commit whose
    // span reaches back INSIDE the previous turn
    t.handleCommit({ speakerId: 's1', tStartMs: 2000, tEndMs: 7000 });
    await drain();
    const s0segs = confirmed.filter(c => c.speaker === 's0').flatMap(c => c.segments);
    const s1segs = confirmed.filter(c => c.speaker === 's1').flatMap(c => c.segments);
    const s0max = Math.max(...s0segs.map(s => s.endMs));
    check('highwater: s0 confirmed to its boundary', s0segs.length > 0 && s0max <= 4001, `${s0max}`);
    check('highwater: s1 never re-enters confirmed audio',
      s1segs.every(s => s.startMs >= 4000) &&
      ((t as any).turn === null || (t as any).turn.t0 >= 4000),
      JSON.stringify(s1segs.map(s => [s.startMs, s.endMs])));
  }

  // 9. turn-level re-clustering merges an oversplit cluster (same voice)
  {
    const { t, renamed } = await makeT();
    const embA = [1, 0, 0];
    const embB = [0, 1, 0];
    t.feedAudio(tone(5000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000, emb: embA });
    t.feedAudio(tone(4000), 5000);
    t.handleCommit({ speakerId: 's1', tStartMs: 4500, tEndMs: 8000, emb: embA }); // SAME voice, oversplit id
    t.feedAudio(tone(4000), 9000);
    t.handleCommit({ speakerId: 's2', tStartMs: 8500, tEndMs: 12000, emb: embB }); // different voice
    await drain();
    t.dispose(); // closes the last turn → 3 records → recluster
    await drain();
    check('recluster: oversplit merged back', renamed.some(r => r.from === 's1' && r.to === 's0'),
      JSON.stringify(renamed.map(r => [r.from, r.to])));
    check('recluster: different voice untouched', !renamed.some(r => r.from === 's2' || r.to === 's2'));
  }

  // 10. strict serialization across drafts and closing passes
  {
    let active = 0; let overlapped = false; let count = 0;
    const { t } = await makeT();
    (t as any).cb.transcribe = async (pcm: Float32Array) => {
      active++; if (active > 1) overlapped = true;
      await new Promise(r => setTimeout(r, 5));
      active--; count++;
      return stableHeadFake(pcm, count);
    };
    t.feedAudio(tone(16000), 0);
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 4000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 4200, tEndMs: 8000 });
    t.handleCommit({ speakerId: 's1', tStartMs: 8200, tEndMs: 12000 });
    await new Promise(r => setTimeout(r, 120));
    check('serial: never concurrent', !overlapped);
    // draft (to live edge), identical-window skip, closing pass, new-turn draft
    check('serial: all passes ran', count === 3, `${count}`);
  }

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
