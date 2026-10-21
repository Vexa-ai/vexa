/**
 * Regression — a turn close never finalizes a transcript that misses buffered audio (Zoom per-track).
 *
 * A turn keeps receiving frames while a transcription request is answered, so `lastTranscript`
 * describes the window up to the PREVIOUS request, not the whole buffer. `flushSpeaker` used to emit
 * that text and reset the buffer whenever any transcript existed: the audio fed after that request
 * never reached transcription. On a starved Zoom page every turn ends this way, and the tail of
 * each turn was lost.
 *
 * With `callbackStampedFrames`, the close emits `lastTranscript` only when it covers every buffered
 * sample; otherwise it submits the whole window as the final one (or, with a request in flight,
 * resubmits it when that response lands). Feeders without the flag keep today's flush.
 *
 * Model-free and timer-free: requests are issued through the manager's own submit path, answered
 * by hand, and the consumer is modelled as an upsert-by-id store.
 *
 *   tsx src/turn-close-flush.test.ts
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

const SR = 16000, FRAME = 4096, FRAME_MS = (FRAME / SR) * 1000;
const speech = (): Float32Array => { const f = new Float32Array(FRAME); for (let i = 0; i < FRAME; i++) f[i] = 0.1 * Math.sin(i / 7); return f; };
const one = (text: string, end: number) => [{ start: 0, end, text }];

function harness(config?: { callbackStampedFrames?: boolean }) {
  const mgr = new SpeakerStreamManager(config);
  const requests: number[] = [];                                   // frames per transcription request
  const store = new Map<string, { text: string; completed: boolean }>();   // consumer: upsert by id
  mgr.onSegmentReady = (_id, _name, audio) => { requests.push(audio.length / FRAME); };
  mgr.onSegmentPending = (id, _n, text, startMs) => {
    const key = `${id}:${Math.round(startMs)}`;
    if (!text.trim()) store.delete(key); else store.set(key, { text, completed: false });
  };
  mgr.onSegmentConfirmed = (id, _n, text, startMs) => { store.set(`${id}:${Math.round(startMs)}`, { text, completed: true }); };
  const submit = (sid: string) => (mgr as unknown as { trySubmit(id: string): Promise<void> }).trySubmit(sid);
  let t = 1_800_000_000_000;
  const feed = (sid: string, n: number) => { for (let k = 0; k < n; k++) { mgr.feedAudio(sid, speech(), t); t += FRAME_MS; } };
  const confirmed = () => [...store.values()].filter((r) => r.completed).map((r) => r.text);
  const drafts = () => [...store.values()].filter((r) => !r.completed).length;
  return { mgr, requests, feed, submit, confirmed, drafts };
}
const ZOOM = { callbackStampedFrames: true } as const;

// ── A: audio arrives after the last answered request, then the turn closes ────────────────────
{
  const h = harness(ZOOM); const SID = 'ch-4:1';
  h.mgr.addSpeaker(SID, 'Speaker');
  h.feed(SID, 8);                                             // 2.05 s
  await h.submit(SID);                                        // request 1: 8 frames
  h.mgr.handleTranscriptionResult(SID, 'early words', 2.0, one('early words', 2.0), 'en');
  h.feed(SID, 4);                                             // 1 s more speech, not yet transcribed
  await h.mgr.flushSpeaker(SID, true);                        // turn closes
  ok(h.requests.length === 2 && h.requests[1] === 12,
    `the close submits the whole 12-frame window as the final request (requests: ${JSON.stringify(h.requests)})`);
  h.mgr.handleTranscriptionResult(SID, 'early words and later words', 3.0, one('early words and later words', 3.0), 'en');
  ok(h.confirmed().length === 1 && h.confirmed()[0] === 'early words and later words',
    `the turn is finalized from the covering request, not the stale text (confirmed: ${JSON.stringify(h.confirmed())})`);
  ok(h.drafts() === 0, 'the draft row is replaced under its own id — nothing dangles as completed:false');
  h.mgr.removeAll();
}

// ── B: the last transcript already covers every buffered sample — emitted at once, no new request ──
{
  const h = harness(ZOOM); const SID = 'ch-4:2';
  h.mgr.addSpeaker(SID, 'Speaker');
  h.feed(SID, 8);
  await h.submit(SID);
  h.mgr.handleTranscriptionResult(SID, 'all of it', 2.0, one('all of it', 2.0), 'en');
  await h.mgr.flushSpeaker(SID, true);
  ok(h.requests.length === 1, `a covering transcript closes the turn without another request (requests: ${JSON.stringify(h.requests)})`);
  ok(h.confirmed().length === 1 && h.confirmed()[0] === 'all of it', 'the covering transcript is the final text');
  h.mgr.removeAll();
}

// ── C: a newer request is in flight and more audio arrives before the close ─────────────────────
// lastTranscript covers 8 frames, request 2 (12 frames) is in flight, 2 more frames arrive, the turn
// closes: the close must wait for request 2 and then resubmit all 14 frames as the final window.
{
  const h = harness(ZOOM); const SID = 'ch-5:1';
  h.mgr.addSpeaker(SID, 'Speaker');
  h.feed(SID, 8);
  await h.submit(SID);
  h.mgr.handleTranscriptionResult(SID, 'early words', 2.0, one('early words', 2.0), 'en');
  h.feed(SID, 4);
  await h.submit(SID);                                        // request 2: 12 frames, in flight
  h.feed(SID, 2);
  await h.mgr.flushSpeaker(SID, true);
  ok(h.confirmed().length === 0, `nothing stale is finalized while a newer request is in flight (confirmed: ${JSON.stringify(h.confirmed())})`);
  h.mgr.handleTranscriptionResult(SID, 'early words and more', 3.0, one('early words and more', 3.0), 'en');   // request 2 lands
  ok(h.requests.length === 3 && h.requests[2] === 14, `the owned 14-frame window is resubmitted as the final request (requests: ${JSON.stringify(h.requests)})`);
  h.mgr.handleTranscriptionResult(SID, 'early words and more and the end', 3.5, one('early words and more and the end', 3.5), 'en');
  ok(h.confirmed().length === 1 && h.confirmed()[0] === 'early words and more and the end', 'the final window is what the turn keeps');
  h.mgr.removeAll();
}

// ── D: feeders that do not opt in keep today's flush ──────────────────────────────────────────
{
  const h = harness(); const SID = 'ch-6:1';
  h.mgr.addSpeaker(SID, 'Speaker');
  h.feed(SID, 8);
  await h.submit(SID);
  h.mgr.handleTranscriptionResult(SID, 'early words', 2.0, one('early words', 2.0), 'en');
  h.feed(SID, 4);
  await h.mgr.flushSpeaker(SID, true);
  ok(h.requests.length === 1 && h.confirmed()[0] === 'early words',
    'without the flag the close emits the last transcript as before (unchanged default path)');
  h.mgr.removeAll();
}

console.log(`\n✅ turn-close-flush: ${checks} checks passed`);
