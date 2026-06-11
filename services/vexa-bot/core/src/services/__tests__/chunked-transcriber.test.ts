/**
 * ChunkedTranscriber unit tests — drives the cut/merge/turn/attribution
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
  const confirmed: Array<{ speaker: string; segments: ChunkSegment[] }> = [];
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
        const text = opts?.text ? opts.text(n++) : `so we kept talking about point number ${n++} for a while`;
        return { text, language: 'en', duration: pcm.length / SR, segments: [{ text, start: 0, end: pcm.length / SR }] };
      },
      publish: (speaker: string, segments: ChunkSegment[]) => confirmed.push({ speaker, segments }),
      publishPending: (speaker: string, segments: ChunkSegment[]) => pending.push({ speaker, segments: [...segments] }),
      clearPending: (speaker: string) => cleared.push(speaker),
      rename: (from: string, to: string, segments: ChunkSegment[]) => renamed.push({ from, to, segments }),
    },
    log: () => {},
    binder: new (await import('../cluster-name-binder')).ClusterNameBinder({}),
    diarizer: null,
    ring: [], ringMs: 0, carry: null, queue: [], pumping: false,
    lastFinalText: '', chunkCounter: 0, turnCounter: 0, disposed: false,
    turn: null, lastChunkWallMs: 0, idleTimer: null, unresolved: [],
  });
  return { t, calls, confirmed, pending, cleared, renamed };
}

function feed(t: any, tMs: number, pcm: Float32Array) {
  t.feedAudio(pcm, tMs);
}

async function drain() {
  await new Promise(r => setTimeout(r, 15));
}

(async () => {
  // 1. chunk → PENDING draft (fast path), nothing confirmed while turn open
  {
    const { t, calls, confirmed, pending } = await makeT();
    feed(t, 0, tone(3000));
    t.recordHint('Alice', 'dom-active', 100);
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('draft: one whisper call', calls.length === 1);
    check('draft: exact span samples', calls[0]?.samples === 2 * SR, `got ${calls[0]?.samples}`);
    check('draft: published as pending', pending.length === 1 && pending[0].speaker === 'Alice');
    check('draft: nothing confirmed yet', confirmed.length === 0);
  }

  // 2. cluster change closes the turn → ONE final pass over the whole turn → confirmed
  {
    const { t, calls, confirmed, pending } = await makeT();
    feed(t, 0, tone(8000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 2200, tEndMs: 4000 });
    t.handleCommit({ speakerId: 's1', tStartMs: 4200, tEndMs: 6000 });   // closes s0's turn
    await drain();
    // calls: draft c1, draft c2, FINAL turn(s0) [0..4000], draft c3
    check('turn: final pass fired on cluster change', calls.length === 4, `got ${calls.length}`);
    check('turn: final spans whole turn', calls[2]?.samples === 4 * SR, `got ${calls[2]?.samples}`);
    check('turn: confirmed once, turn ids', confirmed.length === 1 && confirmed[0].segments[0].segmentId.startsWith('turn:0:'));
    check('turn: confirmed under cluster id (no hints)', confirmed[0]?.speaker === 's0');
    check('turn: drafts accumulated pending', pending.length >= 2 && pending[1].segments.length === 2);
  }

  // 3. silence gap closes the turn
  {
    const { t, confirmed } = await makeT();
    feed(t, 0, tone(12000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 8000, tEndMs: 10000 }); // gap 6s > 2.5s
    await drain();
    check('gap: turn finalized on silence gap', confirmed.length === 1);
    check('gap: new turn open for second chunk', (t as any).turn !== null && (t as any).turn.t0 === 8000);
  }

  // 4. RMS gate: silent chunk never reaches Whisper
  {
    const { t, calls } = await makeT();
    feed(t, 0, silence(3000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    await drain();
    check('gate: silence dropped before whisper', calls.length === 0);
  }

  // 5. prompt chains from the LAST FINAL text
  {
    const { t, calls } = await makeT();
    feed(t, 0, tone(10000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });
    t.handleCommit({ speakerId: 's1', tStartMs: 2200, tEndMs: 4000 });  // finalizes s0 turn
    await drain();
    check('prompt: drafts before any final have none', calls[0]?.prompt === undefined);
    // calls: draft c1, FINAL s0-turn (prompt undefined), draft c2 (prompt = final text)
    check('prompt: post-final draft chained', !!calls[2]?.prompt && calls[2].prompt!.includes('point number 1'), calls[2]?.prompt);
  }

  // 6. final pass empty → drafts promoted, never lose the turn
  {
    const { t, confirmed } = await makeT({
      text: (i) => (i === 1 ? '' : 'we definitely said something meaningful here'),
    });
    feed(t, 0, tone(8000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 2000 });   // draft (call 0)
    t.handleCommit({ speakerId: 's1', tStartMs: 2200, tEndMs: 4000 }); // final (call 1 → empty)
    await drain();
    check('fallback: drafts promoted on empty final', confirmed.length === 1 && confirmed[0].segments[0].segmentId.startsWith('mix:'));
  }

  // 7. late hint renames a finalized turn, same segment ids
  {
    const { t, confirmed, renamed } = await makeT();
    feed(t, 0, tone(8000));
    t.handleCommit({ speakerId: 'speaker_0', tStartMs: 0, tEndMs: 2000 });
    t.handleCommit({ speakerId: 'speaker_1', tStartMs: 2200, tEndMs: 4000 });
    await drain();
    check('late: provisional turn confirmed', confirmed[0]?.speaker === 'speaker_0');
    t.recordHint('Bob', 'dom-active', 500);
    check('late: renamed on hint', renamed.length === 1 && renamed[0].to === 'Bob');
    check('late: same segment ids', renamed[0]?.segments[0]?.segmentId === confirmed[0]?.segments[0]?.segmentId);
  }

  // 8. strict serialization (drafts + finals share one queue)
  {
    const order: number[] = [];
    const { t } = await makeT();
    let active = 0; let overlapped = false;
    (t as any).cb.transcribe = async (pcm: Float32Array) => {
      active++; if (active > 1) overlapped = true;
      await new Promise(r => setTimeout(r, 5));
      order.push(pcm.length); active--;
      return { text: 'we said real words here', language: 'en', duration: 1, segments: [{ text: 'we said real words here', start: 0, end: 1 }] };
    };
    feed(t, 0, tone(10000));
    t.handleCommit({ speakerId: 's0', tStartMs: 0, tEndMs: 1000 });
    t.handleCommit({ speakerId: 's0', tStartMs: 1200, tEndMs: 3000 });
    t.handleCommit({ speakerId: 's1', tStartMs: 3200, tEndMs: 5000 });
    await new Promise(r => setTimeout(r, 100));
    check('serial: never concurrent', !overlapped);
    check('serial: draft,draft,final,draft order', order.length === 4 && order[2] === 3 * SR, JSON.stringify(order));
  }

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
