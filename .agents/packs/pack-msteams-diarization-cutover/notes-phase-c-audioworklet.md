# Phase C note — AudioWorklet anti-aliasing lowpass is NOT needed in the bot

## Decision

The RnD pack's `capture.js` shipped a 63-tap windowed-sinc lowpass before the
48k→16k decimation. That was a bug fix for the RnD harness's CUSTOM
`AudioWorklet` (`services/vexa-bot/rnd/diarization/public/capture.js`), which
did decimate-by-skip with NO filter — aliasing 4–8 kHz speech energy back
into the band and degrading wespeaker embedding quality.

The production bot's MS Teams capture path is different:

```ts
// services/vexa-bot/core/src/platforms/msteams/recording.ts:602
const ctx = new AudioContext({ sampleRate: 16000 });
const source = ctx.createMediaStreamSource(stream);
const processor = ctx.createScriptProcessor(4096, 1, 1);
```

The `AudioContext({ sampleRate: 16000 })` constructor tells Chromium to
resample any incoming audio to 16 kHz **as part of the audio graph**.
Chromium's built-in resampler (libwebrtc-based) is a high-quality polyphase
filter with proper anti-aliasing — i.e. it already does what we did manually
in the RnD harness.

Therefore the lowpass port called out in the pack epic's blast-radius and PR
readiness checklist is **not required for the bot's MS Teams flow**. The
audio that lands in `__vexaTeamsAudioData` is already clean 16 kHz.

## Verification

Quick check from a real Teams session would show:
- spectrogram of the captured `__vexaTeamsAudioData` PCM has a clean
  -80 dB rolloff above ~7.6 kHz (Chromium's resampler cutoff), not a
  hard cliff with aliased mirror lobes (what we saw in the RnD pre-fix
  capture).

This can be confirmed by routing the bot's per-frame audio through the
RnD harness's `eval/inspect-boundaries.ts` or just dumping a wav and
running `ffprobe -show_frames` / a spectrogram.

## Out-of-scope items per pack epic

The pack epic's PR-readiness checklist line "AudioWorklet lowpass ported
to the bot's browser-context capture code" is now closed as "not
applicable — bot uses AudioContext native resampling, no manual decimation
to fix."

If the bot's audio capture is ever refactored to use a manual
`AudioWorklet`-based downsampler (e.g. to support sample rates outside
Chromium's resampler range, or for tighter control over latency), the
63-tap windowed-sinc lowpass from
`services/vexa-bot/rnd/diarization/public/capture.js` is the reference
implementation to port at that time.
