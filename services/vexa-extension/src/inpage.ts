/**
 * In-page capture (MAIN world).
 *
 * This is the bot's browser-side per-speaker capture loop — the exact live
 * Google Meet path from vexa-bot/core/src/index.ts (the page.evaluate block
 * that wires each participant's <audio>/<video> MediaStream into a
 * ScriptProcessor and emits resampled 16 kHz PCM). The only change: where the
 * bot called window.__vexaPerSpeakerAudioData(index, data) across the
 * Playwright bridge, we postMessage the chunk to the content script, which
 * relays it to the background service worker's WebSocket.
 *
 * It must run in the MAIN world (not the isolated content-script world) so it
 * can read the page-assigned el.srcObject MediaStream objects directly.
 *
 * Speaker attribution is the SHARED module from vexa-bot/core
 * (src/browser/gmeet-speakers.ts) — one algorithm for bot and extension.
 */

import { createGmeetSpeakers, GmeetSpeakers } from '../../vexa-bot/core/src/browser/gmeet-speakers';
import { createZoomSpeakers, ZoomSpeakers } from '../../vexa-bot/core/src/browser/zoom-speakers';
import { createGmeetCapture, GmeetCapture } from '../../vexa-bot/core/src/browser/gmeet-capture';

(() => {
  const TAG = '[vexa-inpage]';
  const TARGET_SAMPLE_RATE = 16000;
  const BUFFER_SIZE = 4096;

  let running = false;
  const contexts: AudioContext[] = [];

  // Per-participant Meet capture — SHARED vexa-bot module (one codebase).
  let gmeetCapture: GmeetCapture | null = null;

  // Reserved high index for the local microphone ("You"), kept clear of the
  // 0-based participant element indices.
  const MIC_INDEX = 1000;
  let micStream: MediaStream | null = null;

  function post(type: string, payload: any) {
    window.postMessage({ __vexa: true, type, ...payload }, '*');
  }

  // Speaker attribution — shared vexa-bot modules (one codebase for bot + extension).
  //   Meet: gmeet-speakers (per-track vote/lock)  → window.__vexaGmeetSpeakers
  //   Zoom: zoom-speakers (active-speaker DOM)     → window.__vexaZoomSpeakers
  // Zoom audio is the mixed tabCapture track at TAB_INDEX; we relabel it with
  // whoever Zoom currently renders as the active speaker.
  let speakers: GmeetSpeakers | null = null;
  let zoomSpeakers: ZoomSpeakers | null = null;

  function startSpeakerAttribution(): void {
    if (location.hostname.endsWith('meet.google.com')) {
      if (speakers) return;
      const selfName = (document.querySelector('[data-self-name]') as HTMLElement | null)
        ?.getAttribute('data-self-name')?.trim() || undefined;
      speakers = createGmeetSpeakers({
        selfName,
        log: (m) => console.log(`${TAG} ${m}`),
        onName: (index, name) => post('speakers', { speakers: { [String(index)]: name } }),
      });
      (window as any).__vexaGmeetSpeakers = speakers;
      console.log(`${TAG} shared gmeet-speakers attribution started (self="${selfName || 'unknown'}")`);
    } else if (location.hostname.endsWith('zoom.us')) {
      if (zoomSpeakers) return;
      const selfName = (document.querySelector('[data-self-name]') as HTMLElement | null)
        ?.getAttribute('data-self-name')?.trim() || undefined;
      zoomSpeakers = createZoomSpeakers({
        selfName,
        log: (m) => console.log(`${TAG} [zoom] ${m}`),
        // Multi-channel: each WebRTC track (injected by the document_start hook)
        // is a participant. Vote track→name via the active-speaker DOM and
        // relabel that track once locked.
        onName: (index, name) => post('speakers', { speakers: { [String(index)]: name } }),
      });
      (window as any).__vexaZoomSpeakers = zoomSpeakers;
      console.log(`${TAG} shared zoom-speakers attribution started (multi-channel, self="${selfName || 'unknown'}")`);
    }
  }

  function streamCount(): number {
    return (gmeetCapture ? gmeetCapture.streamCount() : 0) + (micStream ? 1 : 0);
  }

  // Placeholder label for a freshly-seen participant track until attribution
  // locks a real name. Meet names its own tracks via gmeet-speakers, so skip
  // there; Zoom/Teams tracks (injected by the WebRTC hook) start as "Speaker N".
  const labeledTracks = new Set<number>();
  function labelTrack(index: number): void {
    if (index === MIC_INDEX || location.hostname.endsWith('meet.google.com')) return;
    if (labeledTracks.has(index)) return;
    labeledTracks.add(index);
    post('speakers', { speakers: { [String(index)]: `Speaker ${index + 1}` } });
  }

  /**
   * Capture the LOCAL microphone (your own voice). Remote participants live in
   * page media elements; your own voice does not, so we grab it directly. The
   * Meet origin already holds mic permission, so this won't re-prompt.
   */
  async function startMic(): Promise<void> {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      const source = ctx.createMediaStreamSource(micStream);
      const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!running) return;
        const data = e.inputBuffer.getChannelData(0);
        let maxVal = 0;
        for (let i = 0; i < data.length; i++) {
          const a = Math.abs(data[i]);
          if (a > maxVal) maxVal = a;
        }
        if (maxVal > 0.005) post('audio', { index: MIC_INDEX, pcm: Array.from(data) });
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      contexts.push(ctx);
      post('speakers', { speakers: { [MIC_INDEX]: 'You' } });
      console.log(`${TAG} microphone capture started ("You")`);
    } catch (err: any) {
      console.log(`${TAG} mic capture unavailable: ${err.message}`);
      micStream = null;
    }
  }

  async function start() {
    if (running) return;
    running = true;
    console.log(`${TAG} starting capture`);

    startSpeakerAttribution();

    // Your own voice first — it doesn't depend on other participants being present.
    await startMic();
    post('capture-started', { streams: streamCount() });

    // Per-participant capture via the SHARED vexa-bot module — runs on ALL
    // meeting platforms. Google Meet exposes native per-participant <audio>
    // elements; Zoom/Teams get equivalent ones from the document_start WebRTC
    // hook (each remote track mirrored into a hidden <audio>). So this captures
    // MULTI-CHANNEL everywhere — no mixed tabCapture.
    const isMeeting = location.hostname.endsWith('meet.google.com')
      || location.hostname.endsWith('zoom.us')
      || location.hostname.endsWith('teams.live.com')
      || location.hostname.endsWith('teams.microsoft.com')
      || location.hostname === 'teams.cloud.microsoft';
    if (isMeeting) {
      gmeetCapture = createGmeetCapture({
        log: (m) => console.log(`${TAG} ${m}`),
        onAudio: (index, pcm) => {
          speakers?.reportTrackAudio(index);       // Meet per-track voting
          zoomSpeakers?.reportTrackAudio(index);   // Zoom per-track voting
          labelTrack(index);                       // placeholder until a name locks
          post('audio', { index, pcm: Array.from(pcm) });
        },
      });
      await gmeetCapture.start();
    }

    post('capture-started', { streams: streamCount() });
    console.log(`${TAG} capture started with ${streamCount()} stream(s) (mic + participants)`);
  }

  function stop() {
    if (!running) return;
    running = false;
    if (speakers) { speakers.destroy(); speakers = null; (window as any).__vexaGmeetSpeakers = null; }
    if (zoomSpeakers) { zoomSpeakers.destroy(); zoomSpeakers = null; (window as any).__vexaZoomSpeakers = null; }
    if (gmeetCapture) { gmeetCapture.stop(); gmeetCapture = null; }
    labeledTracks.clear();
    if (micStream) { for (const t of micStream.getTracks()) { try { t.stop(); } catch { /* ignore */ } } micStream = null; }
    for (const ctx of contexts) { try { ctx.close(); } catch { /* ignore */ } }
    contexts.length = 0;
    console.log(`${TAG} capture stopped`);
    post('capture-stopped', {});
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.__vexaControl !== true) return;
    if (data.command === 'vexa-start') start();
    else if (data.command === 'vexa-stop') stop();
  });

  post('inpage-ready', {});
  console.log(`${TAG} loaded`);
})();
