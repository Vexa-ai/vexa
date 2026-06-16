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
import { installRemoteAudioHook } from '@vexa/mixed-capture-core';

installRemoteAudioHook({ log: (m) => console.log(`[vexa-webrtc-hook] ${m}`) });
// NOTE: the audio-architecture probe (audio-probe.ts) is intentionally NOT
// installed. It wrapped window.AudioContext at document_start, which interfered
// with Zoom's worklet audio (user lost meeting playback). Its diagnostic job is
// done — we've established Zoom = mixed worklet, no per-participant. Capture must
// never touch the page's audio graph.
