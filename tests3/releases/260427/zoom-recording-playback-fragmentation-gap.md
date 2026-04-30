# v0.10.5 — Zoom Web recording playback shows fragmented "- - -" waveform

**Status:** deferred to next release. v0.10.5 ships green; this is a UX/dashboard
polish item, not a data-correctness or lifecycle blocker.
**Class:** dashboard recording-viewer rendering / Zoom Web parecord paradigm
**Discovered:** 2026-04-30 during v0.10.5 human-stage validation, helm meeting 12
**Reporter:** human (CEO)
**Deferred-by:** human, same turn

## What

Dashboard recording viewer for Zoom Web meetings shows the audio waveform as a
dashed pattern (`- - - - - - - -`) — visually fragmented, with apparent gaps
between short audio segments. The underlying audio file is intact:

| Field | Value |
|---|---|
| meeting | helm 12 (zoom) |
| chunks | 1 (single WAV, parecord paradigm) |
| file_size_bytes | 695,188 |
| duration_seconds | 21.72 |
| density | 695,188 ÷ 21.72 = 32 KB/s = exactly 16 kHz × 16-bit × mono |
| is_final | true |
| transcripts | 2 (real-time STT pipeline works fine) |

So the WAV is full-density continuous data. The fragmentation is in the
**dashboard's waveform/player rendering**, not in the file.

Affects Zoom only. GMeet + Teams (WebRTC-based audio capture) are continuous
even on empty rooms because peer-track-clone always produces samples.

## Why it happens

Zoom Web bots cannot intercept WebRTC audio (Zoom's WCV uses an obfuscated
SharedArrayBuffer-based audio path, not standard `RTCPeerConnection` track
events). The bot falls back to system-level capture via `parecord` on the
PulseAudio sink that Zoom's audio output is routed to.

`parecord` faithfully captures whatever the system speakers are playing. In
an empty test Zoom room with no speakers (the shape of the helm validation
fixture), the system audio output is genuinely silent for most of the
session — so the captured WAV has long stretches of zero-amplitude samples
interspersed with brief sounds (UI chimes, "you're alone" announcements,
etc.). The dashboard's waveform component renders zero-amplitude as dashes
or empty regions, producing the `- - - - - -` look.

In a real Zoom meeting with active speakers, the parecord output would
look like a normal continuous waveform.

## Why deferred

1. **Data-correctness is fine** — file is intact, transcripts produced
   normally, recording is downloadable and plays. The "problem" is
   purely visualisation in the dashboard player.
2. **Likely the rendering is correct** — the audio genuinely has long
   silences in empty test rooms. Not a bug, a true representation.
3. **v0.10.5 is a robustness release** — the user reproduced this on
   short *test* meetings; expected to be much less visible in real
   customer meetings where humans actually speak.
4. **Fixing properly takes meaningful effort** (see options below) and
   touches code paths outside the v0.10.5 scope.

## What "fixing" would mean (next release)

Three options, none free:

### Option 1 — Smarter waveform component (dashboard side)
Replace the dashes-on-zero rendering with a low-amplitude line (so silence
shows as a flat thin line instead of dashes). Pure UX improvement, no
backend change. **Estimated effort: 1 day**, dashboard work only.

### Option 2 — Server-side silence trim before serving
Add a transcoding pass that compresses long silences before the recording
is served. Concretely: `ffmpeg -af silenceremove=start_threshold=...:start_periods=...`
on download. Reduces file size + makes waveform look continuous. Cost:
loses 1:1 timing fidelity (transcript timestamps no longer match the audio
playhead). **Estimated effort: 0.5 day** + a contract change for transcript
↔ audio sync.

### Option 3 — Use peer-track WebRTC audio for Zoom (best quality, biggest scope)
Pursue Zoom Web's audio interception via the SharedArrayBuffer path or
through Zoom's official APIs (if/when available). Would deliver continuous
peer-track audio identical to GMeet/Teams. **Estimated effort: weeks**,
research-grade work.

## Recommended path for next release

**Option 1** — dashboard waveform component fix. Smallest blast radius,
addresses the user-visible cosmetic without changing recording semantics.

## Status check before ship

This file ships **with v0.10.5** as a known-gap deferral. Do NOT block ship.
After v0.10.5 lands, file as the first issue of the next release scope:
`make release-issue-add ID=zoom-recording-waveform-rendering SOURCE=human ...`
on the next release's `groom` stage.
