/**
 * In-page capture (MAIN world) — Google Meet (per-participant) + Zoom (mixed).
 *
 * GOOGLE MEET: the bot's browser-side per-speaker capture loop — each
 * participant's <audio>/<video> MediaStream is wired into an AudioWorklet that
 * emits resampled 16 kHz PCM. Where the bot called
 * window.__vexaPerSpeakerAudioData(index, data) across the Playwright bridge, we
 * postMessage the chunk to the content script, which relays it to the background
 * service worker's WebSocket (capture.v1 → the desktop ingest).
 *
 * ZOOM (MIXED lane): the inpage does NOT capture audio — the offscreen document
 * captures the whole tab's mixed audio (chrome.tabCapture → channel 999, diarized
 * by the desktop's mixed pipeline). The inpage's only job for Zoom is to emit
 * speaker-name HINTS from the zoom-speakers DOM (post('speaker_activity', …)),
 * which the background relays as capture.v1 active-speaker events to name the
 * mixed clusters server-side.
 *
 * It must run in the MAIN world (not the isolated content-script world) so it
 * can read the page-assigned el.srcObject MediaStream objects directly.
 *
 * Speaker attribution is the SHARED bricks @vexa/gmeet-capture (gmeet-speakers)
 * and @vexa/zoom-capture (zoom-speakers) — one algorithm for bot and extension.
 */

import {
  createGmeetSpeakers, GmeetSpeakers,
  createGmeetCapture, GmeetCapture, GmeetChannelBinder,
  createPcmCaptureNode,
} from '@vexa/gmeet-capture';
import { createZoomSpeakers, ZoomSpeakers, createZoomChat, ZoomChat } from '@vexa/zoom-capture';

(() => {
  const TAG = '[vexa-inpage]';

  // Takeover: if an older inpage instance is alive in this page (extension was
  // reloaded and re-injected), stop it completely before installing this one —
  // otherwise both capture and post duplicate audio/diag messages.
  try { (window as any).__vexaInpageTeardown?.(); } catch { /* old instance gone */ }

  // Capture epoch — the SINGLE source of truth for "who owns capture in this
  // page". Teardown-pointer chaining alone is racy (the pointer is overwritten by
  // each new instance and START can reach several instances), which let multiple
  // instances capture the same <audio> elements → 2-3× duplicated PCM (the
  // captured-audio stutter). Each instance claims a higher epoch on load; any
  // instance that is no longer the newest refuses to post audio and self-stops.
  const myEpoch = (((window as any).__vexaCaptureEpoch as number) || 0) + 1;
  (window as any).__vexaCaptureEpoch = myEpoch;
  const isCurrent = () => (window as any).__vexaCaptureEpoch === myEpoch;

  const TARGET_SAMPLE_RATE = 16000;

  let running = false;
  let countTimer: number | null = null;
  const contexts: AudioContext[] = [];

  // Per-participant Meet capture — SHARED gmeet brick (one codebase, bot + ext).
  let gmeetCapture: GmeetCapture | null = null;
  // Per-channel glow correlation — names each channel from the tile whose glow ONSET
  // aligns with that channel's audio onset (NOT the global glow, which leaks across
  // channels). Fed glow edges from gmeet-speakers; queried per audio frame below.
  const channelBinder = new GmeetChannelBinder();

  // Reserved high index for the local microphone ("You"), kept clear of the
  // 0-based participant element indices.
  const MIC_INDEX = 1000;
  let micStream: MediaStream | null = null;

  function post(type: string, payload: any) {
    window.postMessage({ __vexa: true, type, ...payload }, '*');
  }

  // Speaker attribution — shared bricks (one codebase for bot + extension).
  //   Meet: gmeet-speakers (per-track vote/lock)  → window.__vexaGmeetSpeakers
  //   Zoom: zoom-speakers (active-speaker DOM)     → window.__vexaZoomSpeakers
  // Meet capture emits only "who's lit when" HINTS; the per-participant
  // channel→name BINDING happens via the per-channel glow correlator below.
  // Zoom audio is the mixed offscreen tabCapture track (999); we emit the DOM
  // active-speaker timeline as hints that name those clusters server-side.
  let speakers: GmeetSpeakers | null = null;
  let zoomSpeakers: ZoomSpeakers | null = null;
  let zoomChat: ZoomChat | null = null;

  function startSpeakerAttribution(): void {
    if (location.hostname.endsWith('meet.google.com')) {
      if (speakers) return;
      const selfName = (document.querySelector('[data-self-name]') as HTMLElement | null)
        ?.getAttribute('data-self-name')?.trim() || undefined;
      speakers = createGmeetSpeakers({
        selfName,
        log: (m) => console.log(`${TAG} ${m}`),
        onSpeaking: (name, isEnd) => {
          channelBinder.recordGlow(name, isEnd, Date.now());   // feed the per-channel correlator
          post('speaker_activity', { name, isEnd, kind: 'dom-active' });
        },
      });
      (window as any).__vexaGmeetSpeakers = speakers;
      console.log(`${TAG} shared gmeet-speakers HINT emitter started (self="${selfName || 'unknown'}")`);
    } else if (location.hostname.endsWith('zoom.us')) {
      if (zoomSpeakers) return;
      const selfName = (document.querySelector('[data-self-name]') as HTMLElement | null)
        ?.getAttribute('data-self-name')?.trim() || undefined;
      // Zoom audio is the mixed tabCapture track (999) captured by the offscreen
      // document — the inpage does NOT capture audio here. The server diarizes
      // that track into per-speaker clusters; the DOM active-speaker timeline is
      // sent as timestamped HINTS that name those clusters (never track relabels).
      zoomSpeakers = createZoomSpeakers({
        selfName,
        log: (m) => console.log(`${TAG} [zoom] ${m}`),
        onSpeakerChange: (name) => post('speaker_activity', { name: name || '', isEnd: !name, kind: 'dom-active' }),
      });
      (window as any).__vexaZoomSpeakers = zoomSpeakers;
      console.log(`${TAG} shared zoom-speakers HINT emitter started (self="${selfName || 'unknown'}")`);
      // Chat capture — each message becomes a capture.v1 `chat` event. The chat
      // panel must be open for messages to exist in the DOM.
      if (!zoomChat) {
        zoomChat = createZoomChat({
          log: (m) => console.log(`${TAG} [zoom-chat] ${m}`),
          onMessage: ({ sender, text }) => post('chat-message', { sender, text }),
        });
        (window as any).__vexaZoomChat = zoomChat;
      }
    }
  }

  function streamCount(): number {
    return (gmeetCapture ? gmeetCapture.streamCount() : 0) + (micStream ? 1 : 0);
  }

  /**
   * Capture the LOCAL microphone (your own voice). Remote participants live in
   * page media elements; your own voice does not, so we grab it directly. The
   * Meet origin already holds mic permission, so this won't re-prompt.
   */
  async function startMic(): Promise<void> {
    if (window !== window.top) return; // mic belongs to the top frame only
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      const source = ctx.createMediaStreamSource(micStream);
      // AudioWorklet (audio-thread) — the deprecated ScriptProcessor duplicated
      // mic buffers under main-thread load (the captured-audio stutter).
      const node = await createPcmCaptureNode(ctx, (data) => {
        if (!running) return;
        let maxVal = 0;
        for (let i = 0; i < data.length; i++) { const a = Math.abs(data[i]); if (a > maxVal) maxVal = a; }
        if (maxVal > 0.005 && isCurrent()) post('audio', { index: MIC_INDEX, pcm: Array.from(data) });
      });
      source.connect(node);
      node.connect(ctx.destination);
      contexts.push(ctx);
      post('speakers', { speakers: { [MIC_INDEX]: 'You' } });
      console.log(`${TAG} microphone capture started ("You")`);
    } catch (err: any) {
      console.log(`${TAG} mic capture unavailable: ${err.message}`);
      micStream = null;
    }
  }

  async function start() {
    if (running || !isCurrent()) return;   // a superseded instance never captures
    running = true;
    console.log(`${TAG} starting capture`);

    startSpeakerAttribution();

    // Your own voice first.
    await startMic();
    post('capture-started', { streams: streamCount() });

    // Per-participant in-page capture is GOOGLE MEET ONLY — Meet exposes native
    // per-participant <audio> elements; wire each into the shared gmeet captor.
    // Zoom uses the MIXED path instead: one diarized tab-audio channel (999)
    // captured by the OFFSCREEN document, so the inpage captures no audio for it.
    if (location.hostname.endsWith('meet.google.com')) {
      gmeetCapture = createGmeetCapture({
        log: (m) => console.log(`${TAG} ${m}`),
        // Name this channel by PER-CHANNEL correlation: the tile whose glow ONSET
        // aligned with this channel's audio onset (capture.v1 speakerName). NOT the
        // global glow — that leaks one speaker's name onto every channel. undefined
        // until correlated (UNKNOWN), never a guess.
        onAudio: (index, pcm) => {
          if (!isCurrent()) return;
          // Per-channel ENERGY↔GLOW correlation: this channel's name = the tile whose
          // glow tracks its energy over a window (undefined until confident, never the
          // global glow that leaks across channels).
          let peak = 0; for (let i = 0; i < pcm.length; i++) { const a = Math.abs(pcm[i]); if (a > peak) peak = a; }
          const speakerName = channelBinder.nameForChannel(index, Date.now(), peak);
          post('audio', { index, pcm: Array.from(pcm), speakerName });
        },
      });
      await gmeetCapture.start();
    }

    post('capture-started', { streams: streamCount() });
    console.log(`${TAG} capture started with ${streamCount()} stream(s) (mic + participants)`);

    // Keep the panel's stream count live — the rescan discovers late joiners.
    countTimer = window.setInterval(() => {
      if (!isCurrent()) { stop(); return; }   // a newer instance took over — release capture
      if (running) post('capture-started', { streams: streamCount() });
    }, 5000);
  }

  function stop() {
    if (!running) return;
    running = false;
    if (speakers) { speakers.destroy(); speakers = null; (window as any).__vexaGmeetSpeakers = null; }
    if (zoomSpeakers) { zoomSpeakers.destroy(); zoomSpeakers = null; (window as any).__vexaZoomSpeakers = null; }
    if (zoomChat) { zoomChat.destroy(); zoomChat = null; (window as any).__vexaZoomChat = null; }
    if (gmeetCapture) { gmeetCapture.stop(); gmeetCapture = null; }
    if (countTimer !== null) { clearInterval(countTimer); countTimer = null; }
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

  // ── Telemetry: page-context diagnostics, posted every 5s ────────────────
  // PRIVACY: scrub() replaces every word of any DOM-derived free text with a
  // random word ON EXIT, so transcript/caption content never leaves the page
  // readable. Short speaker names pass through (attribution debugging needs
  // them); anything longer is randomized.
  function scrub(s: string): string {
    return s.replace(/\S+/g, (w) => Array.from({ length: Math.min(w.length, 8) },
      () => 'abcdefghijklmnopqrstuvwxyz'[Math.floor(Math.random() * 26)]).join(''));
  }
  function pageDiag(): any {
    const w = window as any;
    const gs = w.__vexaGmeetSpeakers ? (() => {
      try { const st = w.__vexaGmeetSpeakers.getState(); return { speakingNow: st.speakingNow ?? null, tilesSeen: st.tiles?.length }; }
      catch (e: any) { return { error: String(e?.message || e) }; }
    })() : null;
    const zs = w.__vexaZoomSpeakers ? (() => {
      try {
        const st = w.__vexaZoomSpeakers.getState();
        return {
          active: st.active,
          matchedSelector: st.matchedSelector,
          tiles: (st.tiles || []).slice(0, 10).map((t: any) => ({
            name: (t.name || '').length <= 40 ? t.name : scrub(t.name),
            speakingHints: t.speakingHints,
          })),
          survey: (st.survey || []).map((e: any) => ({
            cls: e.cls, aria: e.aria, text: (e.text || '').length <= 40 ? e.text : scrub(e.text),
          })),
        };
      } catch (e: any) { return { error: String(e?.message || e) }; }
    })() : null;
    return {
      host: location.hostname,
      frame: location.pathname.slice(0, 60),
      top: window === window.top,
      running,
      streams: streamCount(),
      micActive: !!micStream,
      injectedAudioEls: document.querySelectorAll('audio[data-vexa-injected]').length,
      mediaElsWithAudio: Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
        !el.paused && el.srcObject instanceof MediaStream && el.srcObject.getAudioTracks().length > 0).length,
      gmeetSpeakers: gs,
      zoomSpeakers: zs,
    };
  }
  const diagTimer = setInterval(() => { try { post('diag', { diag: pageDiag() }); } catch { /* never break capture */ } }, 5000);
  // DIAGNOSTIC PROBE (opt-in: localStorage.vexaDomProbe='1'): dump the live
  // audio↔tile co-location to confirm whether captured <audio> elements sit inside
  // the named/glowing tiles (direct map) or a separate pool (timing required).
  // Off by default — zero overhead in normal capture. Routed to the desktop.
  const probeTimer = setInterval(() => {
    try {
      if (!localStorage.getItem('vexaDomProbe')) return;
      const gs = (window as any).__vexaGmeetSpeakers;
      if (gs && isCurrent()) post('dom_probe', { probe: gs.probeDom() });
    } catch { /* */ }
  }, 3000);

  // Attribution runs from page load (not capture start): diagnostics see the
  // DOM state immediately, and Zoom's temporal naming is live before/without
  // capture. Idempotent — start() calls it again harmlessly.
  try { startSpeakerAttribution(); } catch (e: any) { console.log(`${TAG} attribution at load failed: ${e?.message}`); }

  // Registered teardown for the next instance's takeover (see top of IIFE).
  (window as any).__vexaInpageTeardown = () => {
    try { stop(); } catch { /* not running */ }
    if (diagTimer !== null) { clearInterval(diagTimer); }
    clearInterval(probeTimer);
    if (speakers) { speakers.destroy(); speakers = null; (window as any).__vexaGmeetSpeakers = null; }
    if (zoomSpeakers) { zoomSpeakers.destroy(); zoomSpeakers = null; (window as any).__vexaZoomSpeakers = null; }
    if (zoomChat) { zoomChat.destroy(); zoomChat = null; (window as any).__vexaZoomChat = null; }
    console.log(`${TAG} instance torn down (superseded)`);
  };

  post('inpage-ready', {});
  console.log(`${TAG} loaded`);
})();
