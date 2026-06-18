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

const MIC_INDEX = 1000;
const TARGET_SAMPLE_RATE = 16000;

let stream: MediaStream | null = null;
let ctx: AudioContext | null = null;

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

// Always respond (even on a thrown rejection) — an un-caught reject leaves the
// message channel open then closes it, surfacing a useless "channel closed"
// error instead of the real failure.
const respond = (p: Promise<{ ok: boolean; error?: string }>, sendResponse: (r: unknown) => void) =>
  p.then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'NOTE_CAPTURE_START') { respond(start(), sendResponse); return true; }
  if (msg.type === 'NOTE_CAPTURE_STOP') { stop(); sendResponse({ ok: true }); }
  return undefined;
});
