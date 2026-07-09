# ADR 0005 — The desktop is the LOCAL recording.v1 receiver (all-in-one path)

**Status:** accepted · 2026-06-19 · enforces **P4, P5, P10**

## Context

`recording.v1` is the meeting-RECORDING contract: a sequence of `{chunk_seq, is_final, format, bytes}`
chunks an ACQUIRE adapter emits (browser `MediaRecorder` via `@vexa/record-chunker`, or node PulseAudio
via `@vexa/recording`), which a RECEIVER assembles into one master media file (`buildRecordingMaster` —
byte-concat for WebM, RIFF header-merge for WAV).

In the **cloud** deployment the bot uploads chunks over HTTP multipart and **meeting-api** is the
receiver: it accumulates per session and assembles the master (the Python twin `recording_codec.py`,
golden-pinned ≡ the Node `buildRecordingMaster`). The **all-in-one desktop** (`@vexa/desktop`) has no
meeting-api, no Postgres, no HTTP upload endpoint — it is the same data-plane bricks composed into one
Node process (P10: a module by default; the cloud splits to services only where a force requires it).
So the all-in-one path needs *a* recording receiver, and it must not invent a second wire: the in-tab
capture path (extension → desktop) is already one ingest WebSocket carrying `capture.v1` audio frames.

## Decision

**The desktop subsumes meeting-api's recording.v1 ASSEMBLY role for the all-in-one path, over the same
contract — no new wire.**

- **Same contract, two transports by deployment (deliberate).** Cloud = HTTP multipart → meeting-api
  assembles. All-in-one = the recording.v1 chunk rides the EXISTING ingest WS, encoded by
  `@vexa/capture-codec` `encodeRecordingChunk` (a `REC1`-magic binary frame). The magic is a large
  positive Int32 — never a real track id and never the audio name-flag — so `decodeRecordingChunk`
  returns `null` on a capture *audio* frame: the receiver tries it first, then falls through to
  `decodeAudioFrame`. One socket, two frame types, self-discriminating.
- **Assembly behind a PORT (P5).** A `RecordingSink` port (`desktop/src/recording-sink.ts`) accumulates
  chunks per session and on `is_final` calls `@vexa/recording` `buildRecordingMaster` (a front door, P6)
  → hands the finished bytes to an injected `onMaster` callback. The port is pure — no WS, no disk — so
  it is L2-unit-testable with an in-memory fake. The WebSocket (frame source) and the filesystem (write
  + the gateway `GET /recordings/{p}/{n}` serve) are ADAPTERS in the composition root (`desktop.ts`),
  never in the port.
- **The extension is an ACQUIRE adapter (P5).** `offscreen.ts` tees the SAME captured tab `MediaStream`
  it already holds into `@vexa/record-chunker` `createRecordingTap` → recording.v1 chunks → encode →
  relayed by `background.ts` over the same ingest WS as audio. Additive: the transcription PCM path is
  untouched; recording is a second consumer of the one stream.

## Consequences

- **Trade-off accepted:** the desktop's WebM master is a dependency-free byte-concat — playable, but
  with no injected top-level duration metadata (the cloud's meeting-api optionally runs ffmpeg for
  that). Seeking is approximate; playback is correct. The desktop stays Docker/ffmpeg-free, matching its
  in-memory-store posture (sqlite/ffmpeg are later refinements, not architecture).
- **One contract, gated once.** recording.v1's truth stays its golden vectors (`@vexa/recording`
  `src/contracts/golden/`, TS ≡ Python). Both receivers — meeting-api's `recording_codec.py` and the
  desktop's `RecordingSink` — assemble the same bytes; neither owns a private wire.
- The desktop ↔ extension boundary is recording.v1 (a contract), §3-compliant. The capture.v1 →
  transcript path and the `VEXA_RECORD_TAPE` tape recorder are undisturbed.
