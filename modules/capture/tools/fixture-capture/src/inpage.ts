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

import { createGmeetSpeakers, GmeetSpeakers } from '../../../src/gmeet-speakers';   // gmeet lane: not yet carved
import { createGmeetCapture, GmeetCapture } from '../../../src/gmeet-capture';      // gmeet lane: not yet carved
import { createZoomSpeakers, ZoomSpeakers, createZoomChat, ZoomChat } from '@vexa/zoom-capture';
import { createTeamsSpeakers, TeamsSpeakers } from '@vexa/teams-capture';

(() => {
  const TAG = '[vexa-inpage]';

  // Takeover: if an older inpage instance is alive in this page (extension was
  // reloaded and re-injected), stop it completely before installing this one —
  // otherwise both capture and post duplicate audio/diag messages.
  try { (window as any).__vexaInpageTeardown?.(); } catch { /* old instance gone */ }
  const TARGET_SAMPLE_RATE = 16000;
  const BUFFER_SIZE = 4096;

  let running = false;
  let countTimer: number | null = null;
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
  let teamsSpeakers: TeamsSpeakers | null = null;
  let zoomChat: ZoomChat | null = null;

  function isTeamsHost(): boolean {
    return location.hostname.endsWith('teams.live.com')
      || location.hostname.endsWith('teams.microsoft.com')
      || location.hostname === 'teams.cloud.microsoft';
  }

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
        // Mixed tab-audio track (999): the server diarizes it into per-speaker
        // clusters; the DOM active-speaker timeline is sent as timestamped
        // HINTS that name those clusters (ClusterNameBinder) — never as direct
        // track relabels.
        onSpeakerChange: (name) => post('speaker_activity', { name: name || '', isEnd: !name, kind: 'dom-active' }),
      });
      (window as any).__vexaZoomSpeakers = zoomSpeakers;
      console.log(`${TAG} shared zoom-speakers attribution started (multi-channel, self="${selfName || 'unknown'}")`);
      // Chat capture — emit each message as a capture.v1 `chat` event. Independent
      // of audio/attribution; the chat panel must be open for messages to exist.
      if (!zoomChat) {
        zoomChat = createZoomChat({
          log: (m) => console.log(`${TAG} [zoom-chat] ${m}`),
          onMessage: ({ sender, text }) => post('chat-message', { sender, text }),
        });
        (window as any).__vexaZoomChat = zoomChat;
      }
    } else if (isTeamsHost()) {
      if (teamsSpeakers) return;
      // Blue-squares (voice-level-stream-outline) detection — the SAME shared
      // module the bot injects. Debounced speaking start/stop per name feeds
      // the server's binder as timestamped dom-outline hints; the mixed
      // tabCapture track's clusters resolve to these names retroactively.
      teamsSpeakers = createTeamsSpeakers({
        log: (m) => console.log(`${TAG} [teams] ${m}`),
        onSpeaking: (name, _id, isEnd) =>
          post('speaker_activity', { name, isEnd, kind: 'dom-outline' }),
      });
      (window as any).__vexaTeamsSpeakers = teamsSpeakers;
      console.log(`${TAG} shared msteams-speakers attribution started (blue squares)`);
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
    if (window !== window.top) return; // mic belongs to the top frame only
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

    // Keep the panel's stream count live — the rescan discovers late joiners.
    countTimer = window.setInterval(() => { if (running) post('capture-started', { streams: streamCount() }); }, 5000);
  }

  function stop() {
    if (!running) return;
    running = false;
    if (speakers) { speakers.destroy(); speakers = null; (window as any).__vexaGmeetSpeakers = null; }
    if (zoomSpeakers) { zoomSpeakers.destroy(); zoomSpeakers = null; (window as any).__vexaZoomSpeakers = null; }
    if (teamsSpeakers) { teamsSpeakers.destroy(); teamsSpeakers = null; (window as any).__vexaTeamsSpeakers = null; }
    if (zoomChat) { zoomChat.destroy(); zoomChat = null; (window as any).__vexaZoomChat = null; }
    if (gmeetCapture) { gmeetCapture.stop(); gmeetCapture = null; }
    if (countTimer !== null) { clearInterval(countTimer); countTimer = null; }
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
    const gs = w.__vexaGmeetSpeakers ? (() => {
      try { const st = w.__vexaGmeetSpeakers.getState(); return { locks: st.locks ?? st.locked ?? null, tilesSeen: st.tiles?.length }; }
      catch (e: any) { return { error: String(e?.message || e) }; }
    })() : null;
    return {
      host: location.hostname,
      frame: location.pathname.slice(0, 60),
      top: window === window.top,
      running,
      streams: streamCount(),
      micActive: !!micStream,
      hookInstalled: !!w.__vexaRemoteAudioHookInstalled,
      injectedAudioEls: document.querySelectorAll('audio[data-vexa-injected]').length,
      peerConnections: (w.__vexa_peer_connections || []).length,
      // Receiver-level probe: do the page's RTCPeerConnections carry AUDIO
      // receivers (per-participant tracks that never fired ontrack)? If yes,
      // true multichannel Zoom is possible via receiver.track mirroring.
      audioReceivers: (w.__vexa_peer_connections || []).slice(0, 16).map((pc: RTCPeerConnection) => {
        try {
          const rs = pc.getReceivers().filter((r: RTCRtpReceiver) => r.track && r.track.kind === 'audio');
          return { state: pc.connectionState, audio: rs.length, live: rs.filter((r: RTCRtpReceiver) => r.track.readyState === 'live').length };
        } catch { return null; }
      }).filter(Boolean),
      mediaElsWithAudio: Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
        !el.paused && el.srcObject instanceof MediaStream && el.srcObject.getAudioTracks().length > 0).length,
      zoomSpeakers: zs,
      gmeetSpeakers: gs,
      zoomChat: w.__vexaZoomChat ? (() => {
        try { const st = w.__vexaZoomChat.getState(); return { matched: st.matchedContainer, seen: st.seen, candidates: st.candidates.filter((c: any) => c.count > 0), sample: st.sample, recent: st.recent.map((m: any) => ({ sender: m.sender, text: (m.text || '').length <= 60 ? m.text : scrub(m.text) })) }; }
        catch (e: any) { return { error: String(e?.message || e) }; }
      })() : null,
      // Deep audio-architecture probe (audio-probe.ts, installed at document_start).
      // This is what reveals where Zoom's audio actually lives.
      probe: (() => {
        const p = w.__vexaProbe;
        if (!p) return null;
        return {
          audioContexts: p.audioContexts.map((c: any) => ({ state: c.state, sr: c.sampleRate, samples: c.samples, peak: Number(c.peak?.toFixed?.(4) ?? c.peak) })),
          gum: p.gum, gdm: p.gdm,
          worklets: p.worklets, wasm: p.wasmInstantiations,
          msSources: p.msSources, msDestinations: p.msDestinations,
          scriptProcessors: p.scriptProcessors, audioWorkletNodes: p.audioWorkletNodes,
          pcAudioStats: p.pcAudioStats,
        };
      })(),
    };
  }
  const diagTimer = setInterval(() => { try { post('diag', { diag: pageDiag() }); } catch { /* never break capture */ } }, 5000);

  // Attribution runs from page load (not capture start): diagnostics see the
  // DOM state immediately, and Zoom's temporal naming is live before/without
  // capture. Idempotent — start() calls it again harmlessly.
  try { startSpeakerAttribution(); } catch (e: any) { console.log(`${TAG} attribution at load failed: ${e?.message}`); }

  // Registered teardown for the next instance's takeover (see top of IIFE).
  (window as any).__vexaInpageTeardown = () => {
    try { stop(); } catch { /* not running */ }
    if (diagTimer !== null) { clearInterval(diagTimer); }
    if (speakers) { speakers.destroy(); speakers = null; (window as any).__vexaGmeetSpeakers = null; }
    if (zoomSpeakers) { zoomSpeakers.destroy(); zoomSpeakers = null; (window as any).__vexaZoomSpeakers = null; }
    if (teamsSpeakers) { teamsSpeakers.destroy(); teamsSpeakers = null; (window as any).__vexaTeamsSpeakers = null; }
    if (zoomChat) { zoomChat.destroy(); zoomChat = null; (window as any).__vexaZoomChat = null; }
    console.log(`${TAG} instance torn down (superseded)`);
  };

  post('inpage-ready', {});
  console.log(`${TAG} loaded`);
})();
