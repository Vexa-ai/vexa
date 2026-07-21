/**
 * The END-OF-STREAM tail: a turn that is still forming when the stream closes must not be
 * published as if it were complete.
 *
 * `flushSpeaker(force)` is the end-of-stream path — the bot leaving, the meeting ending, a
 * channel rotating. At that moment a turn typically has BOTH a draft from the last completed
 * Whisper response AND newer audio that no response has covered yet: the submit timer only fires
 * every `submitInterval`, and a submission that is still in flight blocks the next one.
 *
 * The harm is not merely that the newer audio is lost — it is that the segment emitted in its
 * place is stamped with the buffer's FULL extent. A 10.7s turn whose draft covered the first 2.2s
 * goes out as a 10.68s segment carrying 2.2s of words, so every downstream consumer sees a
 * complete, plausible segment and nothing reports a gap. Measured on real speech through the real
 * lane (services/bot/src/quality.test.ts, local STT): the final turn kept 0.146 of its words with
 * no dead air after it, against 0.951–0.976 when given 8s of dead air.
 *
 * So the invariant: at end-of-stream, audio newer than the draft is SUBMITTED (or deferred to an
 * in-flight response), and the draft is the FALLBACK for when that submission yields nothing —
 * not the first choice. Model-free: `handleTranscriptionResult` is driven directly, no timers, no
 * model, no network.
 *
 *   tsx src/close-tail.test.ts
 */
import { SpeakerStreamManager } from './speaker-streams.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}

const SID = 'ch-1:1';
const RATE = 16000;
/** Audible PCM — the silence gate drops near-silent windows, so the tone must clear it. */
const audio = (sec: number): Float32Array =>
  Float32Array.from({ length: Math.round(sec * RATE) }, (_, i) => Math.sin(i / 8) * 0.3);
const seg = (text: string, end: number) => ({ start: 0, end, text });

// ── A turn with a 2s draft and 8s of newer, never-transcribed audio, closed by end-of-stream ──
{
  const mgr = new SpeakerStreamManager({ minAudioDuration: 2, submitInterval: 2, confirmThreshold: 2, sampleRate: RATE });
  const submitted: number[] = [];
  const confirmedTexts: string[] = [];
  mgr.onSegmentReady = (_id, _name, buf) => { submitted.push(buf.length / RATE); };
  mgr.onSegmentConfirmed = (_id, _name, text) => { confirmedTexts.push(text.trim()); };
  mgr.addSpeaker(SID, 'Boris');

  // 2s of speech, and the response for it lands — this is the draft.
  mgr.feedAudio(SID, audio(2), 0);
  mgr.handleTranscriptionResult(SID, 'picking up from there', 2.0, [seg('picking up from there', 2.0)]);
  submitted.length = 0;   // only the CLOSE path's submissions matter below

  // 8 more seconds arrive; the timer never gets to submit them (in production: still in flight).
  mgr.feedAudio(SID, audio(8), 2000);

  await mgr.flushSpeaker(SID, true);

  ok(submitted.length > 0,
    `end-of-stream submits the audio newer than the draft (submitted ${JSON.stringify(submitted)}s)`);
  ok(submitted.some((s) => s >= 7.5),
    `the final submission carries the whole untranscribed tail, not a sliver (max ${Math.max(0, ...submitted).toFixed(1)}s of 8s)`);
  ok(!confirmedTexts.includes('picking up from there'),
    `the stale 2s draft is not published as the turn's final text (confirmed: ${JSON.stringify(confirmedTexts)})`);

  // The fallback must survive: when that final submission comes back empty, the draft is still
  // published rather than the turn vanishing.
  mgr.handleTranscriptionResult(SID, '', undefined, []);
  ok(confirmedTexts.includes('picking up from there'),
    'an empty final response falls back to the draft, so the turn is never lost outright');
  mgr.removeAll();
}

// ── The same close while a request is IN FLIGHT: defer to the response, never drop the audio ──
{
  const mgr = new SpeakerStreamManager({ minAudioDuration: 2, submitInterval: 2, confirmThreshold: 2, sampleRate: RATE });
  const confirmedTexts: string[] = [];
  let submits = 0;
  mgr.onSegmentReady = () => { submits++; };
  mgr.onSegmentConfirmed = (_id, _name, text) => { confirmedTexts.push(text.trim()); };
  mgr.addSpeaker(SID, 'Boris');

  mgr.feedAudio(SID, audio(2), 0);
  mgr.handleTranscriptionResult(SID, 'picking up from there', 2.0, [seg('picking up from there', 2.0)]);
  mgr.feedAudio(SID, audio(8), 2000);

  await mgr.flushSpeaker(SID, true);
  const before = submits;
  // The response for the pre-close window lands after the close.
  mgr.handleTranscriptionResult(SID, 'picking up from there boris here', 4.0, [seg('picking up from there boris here', 4.0)]);
  ok(submits >= before,
    'a response landing after close never drops the owned audio on the floor');
  ok(confirmedTexts.every((t) => t.length > 0), 'no empty segment is published by the close path');
  mgr.removeAll();
}

console.log(`\n✅ close-tail: ${checks} checks passed — an end-of-stream turn submits its untranscribed audio instead of publishing a stale draft under a full-length span.`);
