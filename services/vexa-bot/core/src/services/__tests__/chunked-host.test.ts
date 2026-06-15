/**
 * chunked-host unit tests — the shared mapping between ChunkedTranscriber
 * callbacks and the frozen publisher envelope.
 *
 * Run: npx tsx src/services/__tests__/chunked-host.test.ts
 */

import { createChunkedHost, mapChunkSegments } from '../chunked-host';
import { ChunkSegment } from '@vexa/pipeline';

let passed = 0;
let failed = 0;
function check(name: string, cond: boolean, detail = '') {
  if (cond) { passed++; console.log(`  PASS  ${name}`); }
  else { failed++; console.log(`  FAIL  ${name} ${detail}`); }
}

interface PublishCall { speaker: string; confirmed: any[]; pending: any[] }

function makeFakePublisher(sessionStartMs: number, sessionUid: string) {
  const calls: PublishCall[] = [];
  const pub: any = {
    sessionStartMs,
    sessionUid,
    publishTranscript: async (speaker: string, confirmed: any[], pending: any[]) => {
      calls.push({ speaker, confirmed, pending });
    },
  };
  return { pub, calls };
}

const SEGS: ChunkSegment[] = [
  { text: 'hello there.', startMs: 1_000_000 + 5_000, endMs: 1_000_000 + 8_000, language: 'en', segmentId: 'turn:0:0' },
  { text: 'how are you?', startMs: 1_000_000 + 8_000, endMs: 1_000_000 + 11_500, language: 'en', segmentId: 'turn:0:1' },
];

(async () => {
  // 1. mapping: ids, relative times, absolute times, completed flags
  {
    const { pub } = makeFakePublisher(1_000_000, 'sess-abc');
    const mapped = mapChunkSegments(pub, 'Alice', SEGS);
    check('map: segment_id prefixed with sessionUid', mapped[0].segment_id === 'sess-abc:turn:0:0');
    check('map: start relative to sessionStartMs', mapped[0].start === 5 && mapped[1].start === 8, `${mapped[0].start}`);
    check('map: end relative', mapped[1].end === 11.5, `${mapped[1].end}`);
    check('map: completed default true', mapped.every(m => m.completed === true));
    check('map: absolute ISO times', mapped[0].absolute_start_time === new Date(1_005_000).toISOString());
    const pendingMapped = mapChunkSegments(pub, 'Alice', SEGS, false);
    check('map: completed=false for pending', pendingMapped.every(m => m.completed === false));
    check('map: speaker stamped', mapped.every(m => m.speaker === 'Alice'));
  }

  // 2. publish: ONE atomic bundle (confirmed + surviving tail)
  {
    const { pub, calls } = makeFakePublisher(1_000_000, 'sess-abc');
    const host = createChunkedHost({
      transcriptionClient: { transcribe: async () => ({ text: '', language: 'en', duration: 0, segments: [] }) } as any,
      segmentPublisher: pub,
      language: () => 'en',
    });
    host.publish('Alice', [SEGS[0]], [SEGS[1]]);
    await new Promise(r => setTimeout(r, 5));
    check('publish: one call', calls.length === 1);
    check('publish: confirmed + pending together', calls[0].confirmed.length === 1 && calls[0].pending.length === 1);
    check('publish: pending marked incomplete', calls[0].pending[0].completed === false);
  }

  // 3. pending-only refresh and clear
  {
    const { pub, calls } = makeFakePublisher(1_000_000, 'sess-abc');
    const host = createChunkedHost({
      transcriptionClient: {} as any, segmentPublisher: pub, language: () => undefined,
    });
    host.publishPending('Bob', SEGS);
    host.clearPending('Bob');
    await new Promise(r => setTimeout(r, 5));
    check('pending: empty confirmed', calls[0].confirmed.length === 0 && calls[0].pending.length === 2);
    check('clear: empty bundle', calls[1].confirmed.length === 0 && calls[1].pending.length === 0);
  }

  // 4. rename = clear old pending + republish SAME ids under new name
  {
    const { pub, calls } = makeFakePublisher(1_000_000, 'sess-abc');
    const host = createChunkedHost({
      transcriptionClient: {} as any, segmentPublisher: pub, language: () => undefined,
    });
    host.rename('speaker_0', 'Carol', SEGS);
    await new Promise(r => setTimeout(r, 5));
    check('rename: clears old name first', calls[0].speaker === 'speaker_0' && calls[0].confirmed.length === 0 && calls[0].pending.length === 0);
    check('rename: republishes under new name', calls[1].speaker === 'Carol' && calls[1].confirmed.length === 2);
    check('rename: SAME segment ids', calls[1].confirmed[0].segment_id === 'sess-abc:turn:0:0');
    check('rename: new speaker stamped', calls[1].confirmed.every((s: any) => s.speaker === 'Carol'));
  }

  // 5. language getter is read per call (bot language can change mid-session)
  {
    let lang: string | undefined = 'en';
    const { pub } = makeFakePublisher(0, 'u');
    const seen: Array<string | undefined> = [];
    const host = createChunkedHost({
      transcriptionClient: { transcribe: async (_p: any, l: any) => { seen.push(l); return { text: '', language: 'en', duration: 0, segments: [] }; } } as any,
      segmentPublisher: pub,
      language: () => lang,
    });
    await host.transcribe(new Float32Array(10), undefined);
    lang = 'de';
    await host.transcribe(new Float32Array(10), undefined);
    check('language: getter re-read per transcribe', seen[0] === 'en' && seen[1] === 'de', JSON.stringify(seen));
    check('language: cb.language live', host.language === 'de');
  }

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
