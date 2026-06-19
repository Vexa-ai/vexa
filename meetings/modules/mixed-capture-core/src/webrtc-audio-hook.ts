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
  // Collect peer connections too (parity with screen-content.ts __vexa_peer_connections).
  win.__vexa_peer_connections = win.__vexa_peer_connections || [];

  const OriginalPC = RTCPeerConnection;

  const handleTrack = (event: RTCTrackEvent) => {
    try {
      if (!event.track || event.track.kind !== 'audio') return;
      const stream = (event.streams && event.streams[0]) || new MediaStream([event.track]);

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
