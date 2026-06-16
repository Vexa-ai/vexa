/**
 * Offscreen document — microphone capture for voice-notepad mode.
 *
 * Lives independently of any tab or the side panel (MV3 offscreen API), so a
 * note keeps recording even if the panel closes. Same capture shape as the
 * in-page mic path: getUserMedia → AudioWorklet → 16 kHz PCM → 'audio'
 * runtime messages, which the background worker already forwards to the
 * ingest WebSocket.
 *
 * NOTE: offscreen documents cannot show permission prompts — the side panel
 * pre-grants mic permission for the extension origin before this runs.
 */

import { createPcmCaptureNode } from '@vexa/gmeet-capture';
import { createMixedAudioCapture, type MixedAudioCapture } from '@vexa/mixed-capture-core';

const MIC_INDEX = 1000;
// Mixed tab audio (all remote participants) — used where the page doesn't expose
// per-participant media elements (Zoom web, Teams). One track, named live by
// the page's active-speaker attribution (zoom-speakers onSpeakerChange).
// 999: distinct from per-participant element indexes (0..N) and the mic (1000).
const TAB_INDEX = 999;
const TARGET_SAMPLE_RATE = 16000;

let stream: MediaStream | null = null;
let ctx: AudioContext | null = null;
let tabStream: MediaStream | null = null;
let tabCapture: MixedAudioCapture | null = null;

async function start(): Promise<{ ok: boolean; error?: string }> {
  if (stream) return { ok: true };
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err: any) {
    return { ok: false, error: err.name || String(err) };
  }
  ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  const source = ctx.createMediaStreamSource(stream);
  // AudioWorklet (audio-thread) — the deprecated ScriptProcessor duplicated mic
  // buffers under load (the captured-audio stutter).
  const node = await createPcmCaptureNode(ctx, (data) => {
    let maxVal = 0;
    for (let i = 0; i < data.length; i++) { const a = Math.abs(data[i]); if (a > maxVal) maxVal = a; }
    if (maxVal > 0.005) {
      chrome.runtime.sendMessage({ type: 'audio', index: MIC_INDEX, pcm: Array.from(data) }).catch(() => { /* ws gone */ });
    }
  });
  source.connect(node);
  node.connect(ctx.destination);
  chrome.runtime.sendMessage({ type: 'speakers', speakers: { [String(MIC_INDEX)]: 'You' } }).catch(() => { /* ignore */ });
  chrome.runtime.sendMessage({ type: 'capture-started', streams: 1 }).catch(() => { /* ignore */ });
  console.log('[vexa-offscreen] mic capture started');
  return { ok: true };
}

function stop(): void {
  if (stream) { for (const t of stream.getTracks()) { try { t.stop(); } catch { /* ignore */ } } stream = null; }
  if (ctx) { try { ctx.close(); } catch { /* ignore */ } ctx = null; }
  console.log('[vexa-offscreen] mic capture stopped');
}

/**
 * Capture the meeting tab's mixed audio output (all remote participants), via a
 * tabCapture stream id minted in the background. This is the platform-agnostic
 * fallback for clients (Zoom web, Teams) that don't expose per-participant
 * <audio> elements. Crucially: tab capture mutes the tab for the user unless we
 * re-output it — so we pipe the stream to the speakers too.
 */
async function startTab(streamId: string): Promise<{ ok: boolean; error?: string }> {
  if (tabStream) return { ok: true };
  try {
    tabStream = await (navigator.mediaDevices as any).getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    });
  } catch (err: any) {
    return { ok: false, error: err.name || String(err) };
  }
  // @vexa/capture brick: 16 kHz PCM for transcription + native-rate re-play so the
  // user keeps hearing the meeting (tab capture otherwise mutes it — "system audio
  // blocked"). Re-play lives in the module so bot + extension share one fix.
  try {
    tabCapture = await createMixedAudioCapture(tabStream!, (pcm) => {
      chrome.runtime.sendMessage({ type: 'audio', index: TAB_INDEX, pcm: Array.from(pcm) }).catch(() => { /* ws gone */ });
    }, { sampleRate: TARGET_SAMPLE_RATE, log: (m) => console.log(`[vexa-offscreen] tab ${m}`) });
  } catch (err: any) {
    if (tabStream) { for (const t of tabStream.getTracks()) { try { t.stop(); } catch { /* */ } } }
    tabStream = null;
    return { ok: false, error: `capture: ${err?.message || err}` };
  }
  chrome.runtime.sendMessage({ type: 'speakers', speakers: { [String(TAB_INDEX)]: 'Participant' } }).catch(() => { /* ignore */ });
  chrome.runtime.sendMessage({ type: 'capture-started', streams: 1 }).catch(() => { /* ignore */ });
  console.log('[vexa-offscreen] tab audio capture started');
  return { ok: true };
}

function stopTab(): void {
  if (tabCapture) { try { tabCapture.stop(); } catch { /* ignore */ } tabCapture = null; }
  if (tabStream) { for (const t of tabStream.getTracks()) { try { t.stop(); } catch { /* ignore */ } } tabStream = null; }
  console.log('[vexa-offscreen] tab audio capture stopped');
}

// Always respond (even on a thrown rejection) — an un-caught reject leaves the
// message channel open then closes it, surfacing a useless "channel closed"
// error instead of the real failure.
const respond = (p: Promise<{ ok: boolean; error?: string }>, sendResponse: (r: unknown) => void) =>
  p.then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'NOTE_CAPTURE_START') { respond(start(), sendResponse); return true; }
  if (msg.type === 'NOTE_CAPTURE_STOP') { stop(); sendResponse({ ok: true }); }
  if (msg.type === 'TAB_CAPTURE_START') { respond(startTab(msg.streamId), sendResponse); return true; }
  if (msg.type === 'TAB_CAPTURE_STOP') { stopTab(); sendResponse({ ok: true }); }
  return undefined;
});
