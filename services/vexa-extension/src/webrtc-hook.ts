/**
 * document_start MAIN-world content script: install the shared WebRTC remote-
 * audio hook BEFORE the page (Zoom/Teams) creates its RTCPeerConnections, so
 * every remote participant's audio track is mirrored into a hidden <audio>
 * element. inpage.ts then captures each one per-participant (multi-channel) via
 * the same per-element path Google Meet uses — no mixed tabCapture needed.
 *
 * Mirrors the bot's platforms/msteams/join.ts addInitScript hook; shares the
 * exact implementation (vexa-bot/core/src/browser/webrtc-audio-hook.ts).
 */
import { installRemoteAudioHook } from '../../vexa-bot/core/src/browser/webrtc-audio-hook';

installRemoteAudioHook({ log: (m) => console.log(`[vexa-webrtc-hook] ${m}`) });
