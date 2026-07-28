# Alloy Meet audio-boundary diagnostic design

**Date:** 2026-07-28  
**Status:** approved for execution  
**Scope:** the blocked Google Meet witness for the Alloy STT code-switch candidate

## Goal

Separate a Google Meet product defect from a defect in the disposable audio speaker. Prove where
the known-good EN → RU → EN WAV first changes before making any production change.

## Constraints

- Keep the candidate STT, Whisper, and API behavior unchanged during stand diagnosis.
- Run the disposable speaker in its own container with its own Chromium process/profile,
  X display, PulseAudio daemon, `tts_sink`, and `virtual_mic`.
- Reuse the existing product recording and `VEXA_CAPTURE_SIGNAL`/`TelemetrySink` taps. Diagnostic
  code may tee or materialize their bytes, but must not introduce a second transport or pipeline.
- Measure one hypothesis at a time. Stop after three disproved hypotheses and review the stand
  architecture before any fourth experiment.
- Do not search volume percentages. The source WAV stays byte-identical across H1 and H2.
- If only the speaker path is damaged, change only the stand. If PCM first becomes invalid at the
  Vexa capture producer, obtain a focused RED before the smallest producer-boundary fix.
- Whisper and API must not compensate for damaged upstream PCM.
- Preserve upstream behavior and the Alloy opt-in contract. Do not push.

## Existing failure and root-cause candidate

The retained witness played the WAV from the same Lite container that hosted the listening Vexa
bot. Lite makes `tts_sink` the default output and remaps `tts_sink.monitor` to `virtual_mic`.
The disposable speaker unmuted both while the listener Chromium rendered Meet remote audio to the
same default sink. That shared graph can feed listener output back into the speaker microphone and
trigger gain/echo behavior before STT.

H1 therefore changes only the stand topology: the speaker moves to a separate container. The WAV,
Meet, candidate image, product flags, and recognition acceptance stay fixed.

## One responsible diagnostic path

```text
mixed-en-ru-en.wav
  -> speaker PulseAudio tts_sink / virtual_mic
  -> Chromium microphone track / WebRTC sender
  -> Google Meet remote stream
  -> Vexa product recording tap
  -> Vexa capture TelemetrySink (Float32, 16 kHz)
  -> existing transcribe tap / Whisper request
  -> Meeting API transcript
```

Runtime artifacts live only under the session-owned ignored directory:

```text
.superpowers/sdd/tmp/alloy-meet-audio-20260728/
```

Every audio boundary produces:

- original or materialized PCM bytes;
- duration and sample count;
- absolute peak and peak dBFS;
- RMS and RMS dBFS;
- clipping sample count;
- SHA-256.

The online Whisper tap currently records request length but not PCM bytes. For diagnostic runs only,
the existing built telemetry adapter may be mounted with a narrowly instrumented copy that adds the
actual request PCM to its existing `.stt.jsonl` record. This is a tee at the existing transcribe
boundary, not a second STT call. It is never part of either final exact-candidate witness.

## Hypotheses

### H1 — the shared PulseAudio graph corrupts the speaker

Expected: a separate guest container keeps source, virtual-mic, and sender PCM unclipped and removes
the clipped/repeated Vexa capture seen in the shared-container stand.

If confirmed, keep the isolated guest topology and proceed to two fresh exact-candidate witnesses.
Do not run H2 or H3.

### H2 — Chromium processing damages an otherwise clean sender

Run only if H1 is disproved. Keep the isolated topology and byte-identical WAV; change only the
speaker microphone constraints by disabling `autoGainControl`, `noiseSuppression`, and
`echoCancellation`.

Expected: virtual-mic PCM remains unchanged while sender/remote PCM becomes materially closer to the
source and the transcript becomes recognizable.

### H3 — WAV format or pre-WebRTC level is invalid

Run only if H1 and H2 are disproved. Inspect the source and virtual-mic capture without changing a
percentage. Normalize only if the measured format or level violates the declared mono 16 kHz,
bounded, unclipped input contract.

If H3 is also disproved, stop and review the stand architecture.

## Acceptance

A calibrated run requires recognizable EN → RU → EN in order, `mul` or honest per-segment languages,
monotone timestamps within the source duration, and no clipping, repeated phrase, or hallucinated
replacement. The final bar is two sequential fresh Google Meet runs with distinct guest names on
the unmodified exact candidate.

Only after those runs pass may the exact candidate receive focused tests, a clean Lite build, the
full Linux gate, evidence update, `git diff --check`, and fast-forward integration.

## DRY and SOLID

The design keeps one audio transport and reuses existing recording, capture, and transcribe taps.
Each runtime helper has one responsibility: drive the guest, materialize a tap, or analyze PCM.
Configuration selects a hypothesis without forking product processing. This avoids duplicated STT
implementations, reduces coupling, and keeps rollback, testing, and cleanup bounded.
