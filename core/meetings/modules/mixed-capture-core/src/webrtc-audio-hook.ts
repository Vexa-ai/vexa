/**
 * Remote-audio WebRTC hook — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright). Patches RTCPeerConnection so that
 * every remote participant's audio track (each a separate WebRTC MediaStream
 * track) is mirrored into a hidden <audio data-vexa-injected> element. The
 * existing per-element capture (gmeet-capture.findMediaElements, BrowserAudio
 * Service) then captures EACH participant separately — i.e. multi-channel,
 * NOT a mix.
 *
 * This is the key insight for clients that don't expose per-participant <audio>
 * in the DOM (Zoom web, MS Teams): the per-participant streams DO exist, at the
 * WebRTC layer. The bot installs this for Teams via page.addInitScript
 * (platforms/msteams/join.ts); the extension installs it at document_start in
 * the MAIN world. MUST run before the page creates its RTCPeerConnections.
 *
 * Consumed by:
 *  - the bot: bundled; Teams (and Zoom) install it pre-navigation.
 *  - the extension: a document_start MAIN-world content script calls it.
 */

export interface WebRtcAudioHookOptions {
  log?: (msg: string) => void;
}

/** The shape a reader needs from one intercepted connection: its receivers. */
export interface ObservedPeerConnection {
  getReceivers?: () => unknown[];
}

/**
 * Stream ids that are NOT a participant — Jitsi's SDP placeholders.
 *
 * Jitsi's signalling carries a permanent dummy audio m-line so the SDP shape stays stable across
 * renegotiation: its MediaStream id is `mixedmslabel` (track `mixedlabelaudio0`), and `default` is
 * the same idea on older builds. The track is `live` and unmuted for the whole meeting and carries
 * DIGITAL SILENCE forever; the real participant audio arrives later on its own stream. This is not
 * our reading of the protocol — lib-jitsi-meet refuses exactly these two ids in `isUserStreamById`
 * before it will treat a remote stream as a person.
 *
 * Mirroring it was not a cosmetic error. Measured on `meet.ffmuc.net` 2026-09-17 with a bot alone
 * in a room where the only other participant was muted: the placeholder was the ONLY mirrored
 * stream, so `__vexaStreamPresence` reported 1 live remote stream while no PCM frame ever crossed.
 * That is row 4 of the deaf-capture guard's table (`aloneness.ts`) — streams present, frames
 * absent — so the guard returned `capture-fault` and HELD the bot open on an empty room instead of
 * resolving `left_alone`, until the 4h `max_bot_time_exceeded` backstop. It also made the bot's
 * own log read `[mixed] capture started over 1 stream(s)`, which is indistinguishable from a
 * working capture and is what sent a live investigation after a transcription-pipeline bug that
 * does not exist.
 */
const PLACEHOLDER_STREAM_IDS = new Set(['mixedmslabel', 'default']);

/** Is this remote stream a real participant (not a Jitsi SDP placeholder)? */
export function isUserStreamId(streamId: string | undefined | null): boolean {
  return typeof streamId === 'string' && streamId.length > 0 && !PLACEHOLDER_STREAM_IDS.has(streamId);
}

/**
 * The peer connections this hook has intercepted, in creation order.
 *
 * The registry itself is not new — `wrapPeerConnection` has always pushed every connection into
 * `window.__vexa_peer_connections`. This is only its ACCESSOR, and it exists so a second observer
 * (one reading the TRANSPORT rather than the tracks) can enumerate receivers WITHOUT patching
 * RTCPeerConnection again: two patches race, and the second wrapper would re-fire `handleTrack`
 * and mirror every track twice — exactly the doubling webrtc-dedup.test.ts exists to prevent.
 *
 * Empty where the hook never installed (no RTCPeerConnection, or a page that builds none), which
 * is an ordinary state rather than an error. Reads `globalThis` so it also answers outside a window.
 */
export function observedPeerConnections(): ObservedPeerConnection[] {
  const list = (globalThis as any)?.__vexa_peer_connections;
  return Array.isArray(list) ? (list as ObservedPeerConnection[]) : [];
}

/**
 * Install the RTCPeerConnection patch (idempotent). Returns true if installed
 * now, false if it was already installed or RTCPeerConnection is unavailable.
 */
export function installRemoteAudioHook(opts: WebRtcAudioHookOptions = {}): boolean {
  const win = window as any;
  const log = (m: string) => { try { (opts.log || win.logBot || (() => {}))(m); } catch { /* ignore */ } };

  if (win.__vexaRemoteAudioHookInstalled || typeof RTCPeerConnection !== 'function') return false;
  win.__vexaRemoteAudioHookInstalled = true;
  win.__vexaInjectedAudioElements = win.__vexaInjectedAudioElements || [];
  win.__vexaCapturedRemoteAudioStreams = win.__vexaCapturedRemoteAudioStreams || [];
  // Dedup: BOTH addEventListener('track') AND the ontrack-setter wrapper below fire handleTrack for the
  // same RTCTrackEvent, so without this each remote track is mirrored twice (duplicate <audio> elements).
  win.__vexaMirroredTrackIds = win.__vexaMirroredTrackIds || new Set();
  // Collect peer connections too (parity with screen-content.ts __vexa_peer_connections).
  win.__vexa_peer_connections = win.__vexa_peer_connections || [];

  const OriginalPC = RTCPeerConnection;

  const handleTrack = (event: RTCTrackEvent) => {
    try {
      if (!event.track || event.track.kind !== 'audio') return;
      if (win.__vexaMirroredTrackIds.has(event.track.id)) return;   // already mirrored (both track paths fire)
      const stream = (event.streams && event.streams[0]) || new MediaStream([event.track]);
      // A Jitsi SDP placeholder is not a participant (see PLACEHOLDER_STREAM_IDS). Refusing it here
      // — the one place a remote stream enters the bot — keeps it out of the mix, out of the
      // recording tap's element walk, and out of the presence oracle at once. NOT recorded in
      // __vexaMirroredTrackIds: if a later renegotiation ever reuses that track id on a real
      // stream, it must still be mirrorable.
      if (!isUserStreamId(stream?.id)) {
        log(`[Audio Hook] ignoring placeholder stream ${stream?.id} (track ${event.track.id}) — not a participant`);
        return;
      }
      win.__vexaMirroredTrackIds.add(event.track.id);

      const audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.muted = false;
      audioEl.volume = 1.0;
      audioEl.dataset.vexaInjected = 'true';
      audioEl.style.position = 'absolute';
      audioEl.style.left = '-9999px';
      audioEl.style.width = '1px';
      audioEl.style.height = '1px';
      audioEl.srcObject = stream;
      audioEl.play?.().catch(() => { /* autoplay may defer */ });

      if (document.body) document.body.appendChild(audioEl);
      else document.addEventListener('DOMContentLoaded', () => document.body?.appendChild(audioEl), { once: true });

      (win.__vexaInjectedAudioElements as HTMLAudioElement[]).push(audioEl);
      (win.__vexaCapturedRemoteAudioStreams as MediaStream[]).push(stream);
      log(`[Audio Hook] mirrored remote audio track ${event.track.id.substring(0, 8)} (${win.__vexaInjectedAudioElements.length} total)`);
    } catch (e: any) {
      log(`[Audio Hook] track error: ${e?.message || e}`);
    }
  };

  function wrapPeerConnection(this: any, ...args: any[]) {
    const pc: RTCPeerConnection = new (OriginalPC as any)(...args);
    (win.__vexa_peer_connections as RTCPeerConnection[]).push(pc);
    pc.addEventListener('track', handleTrack);

    // Also wrap the ontrack setter so a page handler doesn't shadow ours.
    const desc = Object.getOwnPropertyDescriptor(OriginalPC.prototype, 'ontrack');
    if (desc && desc.set) {
      Object.defineProperty(pc, 'ontrack', {
        set(handler: any) {
          if (typeof handler !== 'function') return desc.set!.call(this, handler);
          const wrapped = function (this: RTCPeerConnection, event: RTCTrackEvent) {
            handleTrack(event);
            return handler.call(this, event);
          };
          return desc.set!.call(this, wrapped);
        },
        get: desc.get,
        configurable: true,
        enumerable: true,
      });
    }
    return pc;
  }

  wrapPeerConnection.prototype = OriginalPC.prototype;
  Object.setPrototypeOf(wrapPeerConnection, OriginalPC);
  win.RTCPeerConnection = wrapPeerConnection as any;

  log('[Audio Hook] RTCPeerConnection patched — per-participant remote audio will be mirrored.');
  return true;
}
