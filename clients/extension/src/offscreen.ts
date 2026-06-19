/**
 * Offscreen document — microphone capture for voice-notepad mode.
 *
 * Lives independently of any tab or the side panel (MV3 offscreen API), so a
 * note keeps recording even if the panel closes. Same capture shape as the
 * in-page mic path: getUserMedia → AudioWorklet → 16 kHz PCM → 'audio' runtime
 * messages, which the background worker forwards to the ingest WebSocket.
 *
 * NOTE: offscreen documents cannot show permission prompts — the side panel
 * pre-grants mic permission for the extension origin before this runs.
 */

import { createPcmCaptureNode } from '@vexa/gmeet-capture';
import { createMixedAudioCapture, type MixedAudioCapture } from '@vexa/mixed-capture-core';

const MIC_INDEX = 1000;
// Mixed tab audio (YouTube now): the captured tab is ONE mixed PCM stream →
// channel 999, diarized server-side by the desktop's mixed pipeline (pyannote).
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
 * Capture the media tab's mixed audio output, via a tabCapture stream id minted
 * in the background on the toolbar-click gesture. Used for media tabs (YouTube)
 * that have no per-participant <audio> elements: the whole tab IS the audio.
 *
 * Why here and not in-page: the in-page captor's smooth AudioWorklet is blocked
 * by YouTube's page CSP, leaving only a main-thread ScriptProcessor that stutters
 * under YouTube's heavy main thread → choppy PCM → slow pyannote warm-up. The
 * offscreen is a dedicated, low-load document where the ScriptProcessor (inside
 * @vexa/mixed-capture-core) runs smoothly. Crucially: tab capture mutes the tab
 * for the user unless we re-output it — the brick re-plays the stream too.
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
  // @vexa/mixed-capture-core: 16 kHz PCM for transcription + native-rate re-play so
  // the user keeps hearing the tab (tab capture otherwise mutes it). Re-play lives
  // in the module so bot + extension share one fix.
  try {
    tabCapture = await createMixedAudioCapture(tabStream!, (pcm) => {
      chrome.runtime.sendMessage({ type: 'audio', index: TAB_INDEX, pcm: Array.from(pcm) }).catch(() => { /* ws gone */ });
    }, { sampleRate: TARGET_SAMPLE_RATE, log: (m) => console.log(`[vexa-offscreen] tab ${m}`) });
  } catch (err: any) {
    if (tabStream) { for (const t of tabStream.getTracks()) { try { t.stop(); } catch { /* */ } } }
    tabStream = null;
    return { ok: false, error: `capture: ${err?.message || err}` };
  }
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
