/**
 * short-ui-switch.smoke — a short Zoom active-speaker island immediately after
 * a different published speaker should not stamp a confident wrong name when
 * there is no acoustic embedding to confirm it.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let emit!: (ev: BoundaryEvent) => void;
let call = 0;
const published: { speaker: string; text: string }[] = [];

async function main() {
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => {
      call++;
      const text = call === 1 ? 'I do not listen' : call === 2 ? 'There are many secrets' : 'Now I am talking longer';
      return {
        text, language: 'en', language_probability: 0.99,
        segments: [{ text, start: 0, end: call === 2 ? 2.4 : 4.0, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
      };
    },
    publish: (speaker, confirmed) => { for (const c of confirmed) published.push({ speaker, text: c.text }); },
    publishPending: () => {},
    clearPending: () => {},
    rename: (oldSpeaker, newSpeaker, segs) => {
      for (const s of segs) published.push({ speaker: `${oldSpeaker}->${newSpeaker}`, text: s.text });
    },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emit = onBoundary;
      return { appendFrame: async () => {}, reset: () => {} };
    },
    log: () => {},
  });

  const frame = new Float32Array(1600).fill(0.05);
  const feed = (from: number, to: number) => { for (let t = from; t < to; t += 100) tc.feedAudio(frame, t); };

  feed(1000, 2600);
  tc.recordHint('Anthony B. Nyc', 'dom-active', 1000);
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
  emit({ tMs: 2600, kind: 'speaker→silence', confidence: 0.9 });
  await sleep(200);

  feed(3100, 5700);
  tc.recordHint('Lord Mason', 'dom-active', 3100);
  emit({ tMs: 3100, kind: 'silence→speaker', confidence: 0.9 });
  emit({ tMs: 5700, kind: 'speaker→silence', confidence: 0.9 });
  await sleep(200);

  feed(9500, 13_700);
  tc.recordHint('Lord Mason', 'dom-active', 9500);
  emit({ tMs: 9500, kind: 'silence→speaker', confidence: 0.9 });
  emit({ tMs: 13_700, kind: 'speaker→silence', confidence: 0.9 });
  await tc.dispose();

  const short = published.find((p) => p.text.includes('secrets'));
  const longer = published.find((p) => p.text.includes('talking longer'));
  console.log(JSON.stringify(published, null, 2));
  const ok = short && short.speaker !== 'Lord Mason' && longer?.speaker === 'Lord Mason';
  console.log(ok
    ? 'PASS short-ui-switch: short isolated UI switch stayed provisional; longer speaker turn still binds.'
    : 'FAIL short-ui-switch: short UI island was stamped as the wrong speaker or longer turn did not bind.');
  process.exit(ok ? 0 : 1);
}

main().catch((e) => { console.error(e?.message || e); process.exit(1); });
