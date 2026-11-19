/**
 * Regression — a slow count in a voice note (app.dev, 2026-10-13: "… 9 10 7 7 8 9 10 10 11 11 12 12
 * 12 12 12 12 13 …"). Whisper re-emits the prompt's tail at the start of the next window and loops on
 * a repeated token; the lane published both. And with end-of-utterance finals every window is one
 * word, which the short-text hallucination rule dropped ("Один." was filtered as junk).
 *
 *   tsx src/counting-repeats.test.ts
 */
import { SpeakerStreamManager } from './speaker-streams.js';
import { setLogger } from './log.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
setLogger(() => {});
const SR = 16000;
const voiced = (sec: number) => new Float32Array(Math.round(sec * SR)).fill(0.05);
const quiet = (sec: number) => new Float32Array(Math.round(sec * SR));

function lane() {
  const mgr = new SpeakerStreamManager();
  const confirmed: string[] = [];
  const reqs: number[] = [];
  mgr.onSegmentReady = (_k, _n, _pcm, id) => { reqs.push(id); };
  mgr.onSegmentConfirmed = (_k, _n, text) => confirmed.push(text);
  return { mgr, confirmed, reqs };
}

// A: a one-word final on a window that carries speech is published, not filtered as junk.
{
  const { mgr, confirmed } = lane();
  mgr.addSpeaker('a', 'Me');
  mgr.feedAudio('a', voiced(1.2), 1_800_000_000_000);
  await mgr.flushSpeaker('a', true);
  mgr.handleTranscriptionResult('a', 'Один.', 1.2, [{ text: 'Один.', start: 0.1, end: 0.9 }], 'ru');
  ok(confirmed.join(' ') === 'Один.', `a spoken one-word utterance is kept (${JSON.stringify(confirmed)})`);
  // The same word on a silent window is still treated as junk.
  const s = lane();
  s.mgr.addSpeaker('b', 'Me');
  s.mgr.feedAudio('b', new Float32Array(Math.round(1.2 * SR)).fill(0.003), 1_800_000_000_000);
  await s.mgr.flushSpeaker('b', true);
  s.mgr.handleTranscriptionResult('b', 'Да.', 1.2, [{ text: 'Да.', start: 0, end: 1 }], 'ru');
  ok(s.confirmed.length === 0, `a one-word answer on near-silence is still dropped (${JSON.stringify(s.confirmed)})`);
}

// B: the next window's echo of the previous confirmed tail, and a token loop, are not republished.
{
  const { mgr, confirmed } = lane();
  mgr.addSpeaker('c', 'Me');
  let ts = 1_800_000_000_000;
  mgr.feedAudio('c', voiced(3), ts); ts += 3000;
  await mgr.flushSpeaker('c', true);
  mgr.handleTranscriptionResult('c', '7 8 9 10', 3, [{ text: '7 8 9 10', start: 0, end: 3 }], 'ru');
  mgr.addSpeaker('c2', 'Me');
  mgr.feedAudio('c2', voiced(4), ts);
  await mgr.flushSpeaker('c2', true);
  // A second turn of the same voice; its lane is told the first turn's text as its prompt context.
  (mgr as unknown as { buffers: Map<string, { lastConfirmedText: string }> }).buffers.get('c2')!.lastConfirmedText = '7 8 9 10';
  mgr.handleTranscriptionResult('c2', '9 10 11 12 12 12 12 13', 4, [{ text: '9 10 11 12 12 12 12 13', start: 0, end: 4 }], 'ru');
  ok(confirmed[1] === '11 12 13', `prompt echo stripped and the token loop collapsed (${JSON.stringify(confirmed)})`);
}

console.log(`\n✅ counting-repeats: ${checks} checks passed`);
process.exit(0);
