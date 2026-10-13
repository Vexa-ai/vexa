/**
 * An external voice agent, seated in the meeting through the bot.
 *
 * The agent is somebody else's web page (an OpenAI-Realtime front end, say). It runs in a SECOND
 * page of the bot's own browser, and the bot is its ears, its mouth and its face:
 *
 *   meeting page ──(remote participants, mixed)──▶ loopback RTCPeerConnection ──▶ agent's getUserMedia
 *   meeting page ◀── the agent's voice track ─────────────────────────────────────┘
 *   agent page pixels ──(screenshots)──▶ the bot's virtual camera
 *
 * Both pages are in one browser, so the loopback peer connection never leaves the container and no
 * media server, no second X display and no GStreamer are involved. The agent page is untouched: it
 * asks for a microphone the normal way and plays its voice into an <audio> element the normal way —
 * `getUserMedia` is hooked at document-start to hand it the meeting instead of a device, and its
 * inbound WebRTC track is captured on the way in.
 *
 * Node-only: Playwright drives both pages and carries the SDP between them.
 */
import { execFile } from 'node:child_process';
import { launchPersistentBrowser, type BrowserContext, type Page } from '@vexa/remote-browser';
import type { CameraController } from './camera-controller.js';

/** How often the agent's UI is copied onto the bot's camera. */
const FRAME_INTERVAL_MS = 1200;
/** How long to wait for the agent page to produce a voice track before bridging without one. */
const VOICE_WAIT_MS = 25_000;
/** Where the agent's browser plays: the sink whose monitor is the bot's microphone. The meeting's
 *  own browser plays to the deployment's default sink instead, so the room is never fed back into
 *  the microphone and the meeting's echo canceller has no reason to cancel the agent. */
const AGENT_PULSE_SINK = 'tts_sink';
/** Its own profile — a second browser cannot share the meeting browser's user-data directory. */
const AGENT_PROFILE_DIR = '/tmp/vexa-agent-profile';
/** Flags the agent page needs: a microphone without a prompt, and autoplay for its own voice. */
const AGENT_BROWSER_ARGS = [
  '--no-sandbox',
  '--disable-dev-shm-usage',
  '--use-fake-ui-for-media-stream',
  '--autoplay-policy=no-user-gesture-required',
  '--window-size=1280,720',
];

/** How often to re-push the agent's voice onto the meeting's senders (renegotiation drops it). */
const REATTACH_INTERVAL_MS = 4_000;

export interface AgentBridge {
  /** Open the agent page, bridge audio both ways, put its UI on the camera. Never throws. */
  start(): Promise<void>;
  stop(): Promise<void>;
  state(): { url: string | null; running: boolean; frames: number; attached: number };
}

const log = (m: string): void => console.log(`[bot] agent-bridge: ${m}`);

/**
 * The bot's microphone source is muted between utterances (the room must not hear its own audio
 * looping back through the container's single sink). A seated agent speaks whenever it likes, so
 * the source stays open while it is seated — the meeting's playback is silenced in its place.
 */
function setMicSourceMuted(muted: boolean): void {
  const v = muted ? '1' : '0';
  // BOTH halves, or the agent is silent: a muted sink zeroes the audio before its monitor — which
  // is the microphone — ever sees it. The speak path mutes and unmutes the same pair.
  for (const args of [['set-sink-mute', 'tts_sink', v], ['set-source-mute', 'virtual_mic', v]]) {
    execFile('pactl', args, (e) => {
      if (e) log(`could not ${muted ? 'mute' : 'unmute'} ${args[1]}: ${String(e)}`);
    });
  }
}

/** Page-side: hook the agent's microphone and capture its voice. Runs at document-start. */
function agentInit(): void {
  const w = globalThis as any;
  if (w.__vexaAgentBridge) return;
  const bridge: any = { voiceTrack: null, micStream: null };
  bridge.micReady = new Promise((resolve: (s: any) => void) => { bridge.resolveMic = resolve; });
  bridge.voiceReady = new Promise((resolve: (t: any) => void) => { bridge.resolveVoice = resolve; });
  w.__vexaAgentBridge = bridge;

  // The agent asks for a microphone; it gets the meeting. It waits as long as the bridge needs,
  // which is what an agent that opens before admission should do anyway.
  const media = w.navigator.mediaDevices;
  const originalGetUserMedia = media.getUserMedia.bind(media);
  media.getUserMedia = async (constraints: any) => {
    if (!constraints || !constraints.audio) return originalGetUserMedia(constraints);
    const stream = await bridge.micReady;
    return constraints.video ? originalGetUserMedia(constraints) : stream;
  };

  // The agent's own connection to its backend: its inbound audio track IS the agent's voice.
  // Send this page's SOUND to the sink the bot's microphone listens to. Chromium picks the
  // default output device itself and ignores PULSE_SINK, so the routing is done per element, by
  // the device's own name. One real getUserMedia first: device LABELS stay blank until the page
  // has been granted the microphone, and the label is how the sink is found.
  const routeAudioToMicrophone = async (): Promise<void> => {
    try {
      if (!w.__vexaAgentLabelsUnlocked) {
        const probe = await originalGetUserMedia({ audio: true }).catch(() => null);
        probe?.getTracks?.().forEach((t: any) => t.stop());
        w.__vexaAgentLabelsUnlocked = true;
      }
      const devices = await w.navigator.mediaDevices.enumerateDevices();
      const sink = devices.find((d: any) => d.kind === 'audiooutput' && /TTSAudioSink/i.test(d.label || ''));
      if (!sink) return;
      for (const el of Array.from(w.document.querySelectorAll('audio,video')) as any[]) {
        if (typeof el.setSinkId !== 'function' || el.sinkId === sink.deviceId) continue;
        await el.setSinkId(sink.deviceId).catch(() => { /* the element may not be ready yet */ });
        console.log('BRIDGE agent audio routed to the bot microphone');
      }
    } catch { /* retried on the next tick */ }
  };
  setInterval(() => { void routeAudioToMicrophone(); }, 1000);

  const OriginalPeerConnection = w.RTCPeerConnection;
  const Hooked = function (this: any, ...args: any[]) {
    const pc = new OriginalPeerConnection(...args);
    pc.addEventListener('track', (e: any) => {
      if (e.track?.kind === 'audio' && !bridge.voiceTrack) {
        bridge.voiceTrack = e.track;
        bridge.resolveVoice(e.track);
      }
    });
    return pc;
  };
  Hooked.prototype = OriginalPeerConnection.prototype;
  Object.assign(Hooked, OriginalPeerConnection);
  w.RTCPeerConnection = Hooked;
  w.__vexaAgentOriginalPeerConnection = OriginalPeerConnection;
}

/** Page-side (meeting): mix every remote participant's audio and offer it to the agent. */
async function meetingOffer(): Promise<string | null> {
  const w = globalThis as any;
  const existing = w.__vexaMeetingBridge;
  if (existing?.pc) return null;

  const audio = new (w.AudioContext || w.webkitAudioContext)();
  const destination = audio.createMediaStreamDestination();
  const wired = new Set<string>();
  // Meet, Teams and Jitsi all render every remote participant into a media element whose srcObject
  // is that participant's live stream — the same tap the capture lane uses.
  const wireRemotes = (): number => {
    let added = 0;
    for (const el of Array.from(w.document.querySelectorAll('audio,video')) as any[]) {
      const stream = el.srcObject;
      if (!stream || typeof stream.getAudioTracks !== 'function') continue;
      for (const track of stream.getAudioTracks()) {
        if (wired.has(track.id)) continue;
        wired.add(track.id);
        try {
          audio.createMediaStreamSource(new w.MediaStream([track])).connect(destination);
          added++;
        } catch { /* a track that ended between the scan and the wire */ }
      }
    }
    return added;
  };
  wireRemotes();

  // A PRISTINE RTCPeerConnection, borrowed from a detached iframe: the page's own constructor is
  // the virtual camera's hook, which would push the bot's canvas onto this loopback and — worse —
  // list it in the camera's registry, where the voice attach below would replace the very track
  // that carries the meeting to the agent.
  let PeerConnection = w.RTCPeerConnection;
  try {
    const frame = w.document.createElement('iframe');
    frame.style.display = 'none';
    w.document.documentElement.appendChild(frame);
    if (frame.contentWindow?.RTCPeerConnection) PeerConnection = frame.contentWindow.RTCPeerConnection;
  } catch { /* the page forbids iframes — the hook is tolerable, the guard below is not */ }
  const pc = new PeerConnection();
  pc.__vexaAgentLoopback = true;
  for (const track of destination.stream.getAudioTracks() as any[]) pc.addTrack(track, destination.stream);
  pc.addTransceiver('audio', { direction: 'recvonly' });
  const bridge: any = { pc, audio, destination, wired, voiceTrack: null, rescan: setInterval(wireRemotes, 3000) };
  pc.addEventListener('track', (e: any) => { if (e.track?.kind === 'audio') bridge.voiceTrack = e.track; });
  w.__vexaMeetingBridge = bridge;

  await pc.setLocalDescription(await pc.createOffer());
  await new Promise<void>((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve();
    pc.addEventListener('icegatheringstatechange', () => pc.iceGatheringState === 'complete' && resolve());
    setTimeout(resolve, 3000);
  });
  return pc.localDescription?.sdp ?? null;
}

/** Page-side (agent): answer the meeting, taking its audio as the microphone. */
async function agentAnswer(offerSdp: string): Promise<string | null> {
  const w = globalThis as any;
  const bridge = w.__vexaAgentBridge;
  if (!bridge) return null;
  const PeerConnection = w.__vexaAgentOriginalPeerConnection || w.RTCPeerConnection;
  const pc = new PeerConnection();
  bridge.pc = pc;
  pc.addEventListener('track', (e: any) => {
    if (e.track?.kind === 'audio' && !bridge.micStream) {
      bridge.micStream = new w.MediaStream([e.track]);
      // Chromium feeds Web Audio silence from a remote WebRTC track that no media element is
      // playing, so an agent that reads its microphone through an AudioContext would hear nothing.
      // A silent, detached element plays it: never in the document, so the voice routing (which
      // re-targets every element it finds) cannot send the meeting back into the bot's microphone.
      const tap = new w.Audio();
      tap.muted = true;
      tap.volume = 0;
      tap.srcObject = bridge.micStream;
      tap.play().catch(() => { /* autoplay is allowed in this browser; a refusal only costs Web Audio */ });
      bridge.meetingTap = tap;
      bridge.resolveMic(bridge.micStream);
    }
  });
  await pc.setRemoteDescription({ type: 'offer', sdp: offerSdp });
  // The agent connects to its own backend on load, so its voice track usually exists already.
  const voice = await Promise.race([
    bridge.voiceReady,
    new Promise((resolve) => setTimeout(() => resolve(null), 25_000)),
  ]);
  if (voice) pc.addTrack(voice);
  await pc.setLocalDescription(await pc.createAnswer());
  await new Promise<void>((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve();
    pc.addEventListener('icegatheringstatechange', () => pc.iceGatheringState === 'complete' && resolve());
    setTimeout(resolve, 3000);
  });
  return pc.localDescription?.sdp ?? null;
}

/** Page-side (meeting): accept the answer. */
async function meetingApplyAnswer(answerSdp: string): Promise<boolean> {
  const w = globalThis as any;
  const bridge = w.__vexaMeetingBridge;
  if (!bridge?.pc) return false;
  await bridge.pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
  return true;
}

/**
 * Page-side (meeting): what the platform's OWN outgoing audio track says about itself.
 *
 * `enabled` is how a web app mutes a microphone — it keeps the sender and flips the track off, which
 * is why packets keep trickling (comfort noise) while the room hears nothing. This is the direct
 * read; labels and packet counts were both misleading in live runs.
 */
function micState(): { senders: number; enabled: number; disabled: number; labels: string[]; packets: number } {
  const w = globalThis as any;
  const camera = w.__vexaVirtualCamera;
  const out = { senders: 0, enabled: 0, disabled: 0, labels: [] as string[], packets: 0 };
  for (const pc of camera?.pcs ?? []) {
    if (pc.__vexaAgentLoopback || pc.connectionState === 'closed') continue;
    for (const sender of pc.getSenders?.() ?? []) {
      const track = sender.track;
      if (!track || track.kind !== 'audio') continue;
      out.senders++;
      if (track.enabled) out.enabled++; else out.disabled++;
      if (out.labels.length < 3) out.labels.push(`${track.label || 'unnamed'}:${track.enabled ? 'on' : 'off'}:${track.readyState}`);
    }
  }
  return out;
}

/** Page-side (meeting): total audio packets the bot has sent to the room. */
async function outboundAudioPackets(): Promise<number> {
  const w = globalThis as any;
  const camera = w.__vexaVirtualCamera;
  let packets = 0;
  for (const pc of camera?.pcs ?? []) {
    if (pc.__vexaAgentLoopback) continue;
    try {
      const stats = await pc.getStats();
      stats.forEach((r: any) => {
        if (r.type === 'outbound-rtp' && r.kind === 'audio') packets += r.packetsSent || 0;
      });
    } catch { /* a connection that closed mid-read */ }
  }
  return packets;
}

/**
 * Page-side (meeting): turn the platform's outgoing audio track back on.
 *
 * A mute is `track.enabled = false` on the sender's own track; re-enabling it is what actually
 * lets the room hear the microphone again, whatever the button says.
 */
function enableOutgoingAudio(): number {
  const w = globalThis as any;
  const camera = w.__vexaVirtualCamera;
  let enabled = 0;
  for (const pc of camera?.pcs ?? []) {
    if (pc.__vexaAgentLoopback || pc.connectionState === 'closed') continue;
    for (const sender of pc.getSenders?.() ?? []) {
      const track = sender.track;
      if (track?.kind === 'audio' && !track.enabled) { track.enabled = true; enabled++; }
    }
  }
  return enabled;
}

/** Page-side (meeting): click whatever unmutes this platform, by label. */
function clickUnmute(): boolean {
  const w = globalThis as any;
  const doc = w.document;
  for (const el of Array.from(doc.querySelectorAll('button,[role="button"],div[jsname]')) as any[]) {
    const label = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('data-tooltip') || ''}`;
    if (/turn on microphone|unmute/i.test(label) && typeof el.click === 'function') { el.click(); return true; }
  }
  for (const el of Array.from(doc.querySelectorAll('[data-is-muted="true"]')) as any[]) {
    const label = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('data-tooltip') || ''}`;
    if (!/camera|video/i.test(label) && typeof el.click === 'function') { el.click(); return true; }
  }
  return false;
}

/**
 * Page-side (meeting): how loud the agent's voice is WHERE IT ARRIVES.
 *
 * Splits the question the live run could not: a silent peak means the agent is not really speaking
 * into the loopback, and the microphone is innocent; a loud peak with no outbound packets means the
 * platform is holding the bot muted.
 */
function agentVoicePeak(): number {
  const w = globalThis as any;
  const bridge = w.__vexaMeetingBridge;
  if (!bridge?.voiceTrack) return -1;
  if (!bridge.meter) {
    try {
      const audio = new (w.AudioContext || w.webkitAudioContext)();
      const analyser = audio.createAnalyser();
      analyser.fftSize = 2048;
      audio.createMediaStreamSource(new w.MediaStream([bridge.voiceTrack])).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      bridge.meter = { peak: 0 };
      bridge.meterTimer = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        for (const v of buf) bridge.meter.peak = Math.max(bridge.meter.peak, Math.abs(v - 128));
      }, 200);
    } catch { return -2; }
  }
  const peak = bridge.meter.peak;
  bridge.meter.peak = 0;                 // report the peak SINCE THE LAST READ, not since boot
  return peak;
}

/**
 * Page-side (meeting): mute the meeting's PLAYBACK.
 *
 * The container has one sink, and the bot's microphone is that sink's monitor — so anything the
 * page plays out loud comes back in as the bot talking. Capture is unaffected: the lanes read each
 * participant's stream through WebAudio, never through the speakers.
 */
function silenceMeetingPlayback(): number {
  const w = globalThis as any;
  let muted = 0;
  for (const el of Array.from(w.document.querySelectorAll('audio,video')) as any[]) {
    if (!el.muted) { el.muted = true; muted++; }
  }
  if (!w.__vexaPlaybackSilencer) {
    w.__vexaPlaybackSilencer = setInterval(() => {
      for (const el of Array.from(w.document.querySelectorAll('audio,video')) as any[]) el.muted = true;
    }, 2000);
  }
  return muted;
}

/** Page-side (meeting): drop the bridge. */
function meetingStop(): void {
  const w = globalThis as any;
  const bridge = w.__vexaMeetingBridge;
  if (!bridge) return;
  try { clearInterval(bridge.rescan); } catch { /* */ }
  try { bridge.pc?.close(); } catch { /* */ }
  try { bridge.audio?.close(); } catch { /* */ }
  w.__vexaMeetingBridge = null;
}

export function createAgentBridge(page: Page, camera: CameraController, url: string | undefined): AgentBridge {
  const target = (url || '').trim();
  let agentPage: Page | null = null;
  let agentContext: BrowserContext | null = null;
  let pump: ReturnType<typeof setInterval> | null = null;
  let reattach: ReturnType<typeof setInterval> | null = null;
  let frames = 0;
  let attached = 0;
  let running = false;

  const pushFrame = async (): Promise<void> => {
    if (!agentPage || agentPage.isClosed()) return;
    const shot = await agentPage.screenshot({ type: 'jpeg', quality: 45 }).catch(() => null);
    if (!shot) return;
    const drawn = await camera.showFrame(`data:image/jpeg;base64,${shot.toString('base64')}`);
    if (drawn) frames++;
  };

  /**
   * Get the room hearing the agent, and PROVE it: the bot joins muted (the join flow mutes the
   * microphone, right for a recorder), and a muted platform transmits nothing however many senders
   * carry the agent's track. Click what looks like unmute, then keyboard-toggle, and after each
   * attempt read outbound audio packets — the only evidence that is not a label.
   */
  const unmuteUntilHeard = async (): Promise<void> => {
    const read = (): Promise<{ senders: number; enabled: number; disabled: number; labels: string[] }> =>
      page.evaluate(micState).catch(() => ({ senders: 0, enabled: 0, disabled: 0, labels: [] }));

    const before = await read();
    log(`outgoing audio track(s): ${before.enabled} on, ${before.disabled} off — ${before.labels.join(', ') || 'none found'}`);
    // NOT `track.enabled`: this bot enables that track itself, so reading it back proves only that our
    // own write landed. The room hearing the agent is packets leaving the connection, so that is the
    // test — a platform that is muted keeps its senders and sends nothing.
    const sent = async (): Promise<number> => page.evaluate(outboundAudioPackets).catch(() => 0);
    const first = await sent();
    await new Promise((r) => setTimeout(r, 1500));
    if (before.senders > 0 && (await sent()) > first) { log('the microphone is open — the room hears the agent'); return; }

    // Ways to open it, most specific first; the track's own `enabled` is the verdict. Ctrl+D is
    // Google Meet's mute TOGGLE: pressed while an unmute is still landing it mutes the room again,
    // so Meet never gets it.
    const onGoogleMeet = page.url().includes('meet.google.com');
    const attempts: Array<[string, () => Promise<unknown>]> = [
      ['the mute button', () => page.evaluate(clickUnmute)],
      ...(onGoogleMeet ? [] : [['ctrl+d', () => page.keyboard.press('Control+d')] as [string, () => Promise<unknown>]]),
      ['the track itself', () => page.evaluate(enableOutgoingAudio)],
    ];
    for (const [what, act] of attempts) {
      const mark = await sent();
      await act().catch(() => null);
      await new Promise((r) => setTimeout(r, 1500));
      const after = await read();
      const moved = (await sent()) - mark;
      log(`unmute via ${what}: ${after.enabled} on, ${after.disabled} off, ${moved} packet(s) sent`);
      if (after.senders > 0 && moved > 0) { log('the microphone is open — the room hears the agent'); return; }
    }
    log('the microphone would not open — the room cannot hear the agent');
  };

  return {
    state: () => ({ url: target || null, running, frames, attached }),

    async start(): Promise<void> {
      if (!target || running) return;
      running = true;
      try {
        // NO grantPermissions here. The browser launches with --use-fake-ui-for-media-stream, which
        // auto-accepts media prompts for EVERY origin; an explicit per-origin grant replaces that
        // context's overrides and takes the meeting's own microphone away — Meet then shows
        // "Microphone problem" and drops its mic button, which is how this was found.
        // A browser of its OWN, with its own PulseAudio sink: everything this browser plays goes
        // into tts_sink, whose monitor is the bot's microphone, so the agent is heard in the
        // meeting. The meeting's browser keeps the default sink and is never fed back to itself.
        const launched = await launchPersistentBrowser({
          dataDir: AGENT_PROFILE_DIR,
          args: AGENT_BROWSER_ARGS,
          env: { ...process.env, PULSE_SINK: AGENT_PULSE_SINK } as Record<string, string>,
        });
        agentContext = launched.context;
        agentPage = launched.page;
        log(`agent browser up (audio → ${AGENT_PULSE_SINK} → the bot's microphone)`);
        // The camera canvas is 1280x720 and draws a frame `contain`-fit: any other shape here is
        // black bars on the bot's tile.
        await agentPage.setViewportSize({ width: 1280, height: 720 }).catch(() => {});
        agentPage.on('console', (m) => {
          const text = m.text();
          if (/error|fail/i.test(text)) log(`agent page: ${text.slice(0, 200)}`);
        });
        await agentPage.addInitScript(agentInit);
        await agentPage.goto(target, { waitUntil: 'domcontentloaded', timeout: 60_000 });
        log(`agent page open: ${target}`);

        const offer = await page.evaluate(meetingOffer);
        if (!offer) { log('the meeting page produced no offer — is the bridge already up?'); return; }
        const answer = await agentPage.evaluate(agentAnswer, offer);
        if (!answer) { log('the agent page produced no answer — its microphone hook did not install'); return; }
        await page.evaluate(meetingApplyAnswer, answer);
        log('loopback established: the meeting is the agent\'s microphone');

        // The agent's voice reaches the room the way the bot's speech already does: it plays out of
        // the agent page into the container's only sink, whose monitor IS the bot's microphone. The
        // meeting's own playback is silenced first, or the room would hear itself.
        const silenced = await page.evaluate(silenceMeetingPlayback).catch(() => -1);
        log(`meeting playback silenced (${silenced} element(s)) — capture is unaffected`);
        setMicSourceMuted(false);

        const push = async (): Promise<void> => {
          setMicSourceMuted(false);                    // `speak` re-mutes it when it finishes
          const state = await page.evaluate(micState).catch(() => null);
          if (state && state.disabled > 0) {
            // The platform muted US: its own mute state is what stops the room hearing the agent, and
            // a track flip does not change it — the platform simply mutes the track again on its next
            // tick. Press the platform's control first, then flip the track for the gap in between.
            const clicked = await page.evaluate(clickUnmute).catch(() => false);
            const reopened = await page.evaluate(enableOutgoingAudio).catch(() => 0);
            log(`the platform re-muted the microphone; ${clicked ? 'pressed its unmute and ' : ''}re-opened ${reopened} track(s)`);
          }
          attached = await page.evaluate(outboundAudioPackets).catch(() => 0);
        };
        await push();
        await unmuteUntilHeard();
        reattach = setInterval(() => { void push(); }, REATTACH_INTERVAL_MS);

        await pushFrame();
        pump = setInterval(() => { void pushFrame(); }, FRAME_INTERVAL_MS);
        log(`the agent is seated (its UI is on the camera every ${FRAME_INTERVAL_MS}ms)`);
      } catch (e) {
        // Never evict: a bot whose agent failed still transcribes the meeting.
        log(`could not seat the agent: ${String(e)}`);
      }
    },

    async stop(): Promise<void> {
      running = false;
      setMicSourceMuted(true);
      if (pump) { clearInterval(pump); pump = null; }
      if (reattach) { clearInterval(reattach); reattach = null; }
      await page.evaluate(meetingStop).catch(() => {});
      if (agentContext) await agentContext.close().catch(() => {});
      agentContext = null;
      agentPage = null;
      log(`stopped after ${frames} frame(s)`);
    },
  };
}
