/**
 * Offscreen document — microphone capture for voice-notepad mode.
 *
 * Lives independently of any tab or the side panel (MV3 offscreen API), so a
 * note keeps recording even if the panel closes. Same capture shape as the
 * in-page mic path: getUserMedia → ScriptProcessor → 16 kHz PCM → 'audio'
 * runtime messages, which the background worker already forwards to the
 * ingest WebSocket.
 *
 * NOTE: offscreen documents cannot show permission prompts — the side panel
 * pre-grants mic permission for the extension origin before this runs.
 */

const MIC_INDEX = 1000;
const TARGET_SAMPLE_RATE = 16000;
const BUFFER_SIZE = 4096;

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
  const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
  processor.onaudioprocess = (e: AudioProcessingEvent) => {
    const data = e.inputBuffer.getChannelData(0);
    let maxVal = 0;
    for (let i = 0; i < data.length; i++) {
      const a = Math.abs(data[i]);
      if (a > maxVal) maxVal = a;
    }
    if (maxVal > 0.005) {
      chrome.runtime.sendMessage({ type: 'audio', index: MIC_INDEX, pcm: Array.from(data) }).catch(() => { /* ws gone */ });
    }
  };
  source.connect(processor);
  processor.connect(ctx.destination);
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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'NOTE_CAPTURE_START') {
    start().then(sendResponse);
    return true; // async response
  }
  if (msg.type === 'NOTE_CAPTURE_STOP') {
    stop();
    sendResponse({ ok: true });
  }
  return undefined;
});
