/**
 * golden.js — the props the L4 fixture mounts the players with, and the values the spec asserts.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixture page (players-render.html) imports these and mounts AudioPlayer/VideoPlayer with them
 *   • the spec (players-render.spec.ts) imports these and asserts the rendered <audio>/<video> + controls
 *
 * GOLDEN_AUDIO_SRC / GOLDEN_VIDEO_SRC are tiny data: URLs — self-contained, no network, no real codec
 * needed (the L4 gate asserts the ELEMENT + src + a play control render, not that CI decodes media).
 * The bytes are a minimal valid WAV / a 1-byte mp4 stub: enough to be a real `src` attribute value.
 */

// A minimal valid 44-byte WAV header (RIFF/WAVE, 8kHz mono, zero data) as a data: URL.
// Real, parseable container bytes so the <audio src> is a legitimate media URL.
export const GOLDEN_AUDIO_SRC =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

// A tiny data: URL standing in for a video source (no decode required in CI).
export const GOLDEN_VIDEO_SRC = "data:video/mp4;base64,AAAAGGZ0eXBpc29t";

// Multi-fragment golden: two ordered fragments forming one stitched virtual timeline.
export const GOLDEN_FRAGMENTS = [
  {
    src: GOLDEN_AUDIO_SRC,
    duration: 12,
    sessionUid: "sess-1",
    createdAt: "2026-06-22T10:00:00Z",
  },
  {
    src: GOLDEN_AUDIO_SRC,
    duration: 18,
    sessionUid: "sess-2",
    createdAt: "2026-06-22T10:05:00Z",
  },
];

// Expected stitched total duration label for the multi-fragment case: 12 + 18 = 30s → "0:30".
export const GOLDEN_FRAGMENTS_TOTAL_LABEL = "0:30";

// Expected fragment indicator text on first mount: "1/2".
export const GOLDEN_FRAGMENT_INDICATOR = "1/2";
