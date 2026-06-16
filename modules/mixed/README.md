# mixed/ — the mixed-audio lane (Zoom · Teams · any single mixed stream)

One mixed audio stream; speakers are separated by **pyannote segmentation** and
named **after Whisper** from time-windowed platform hints. No diarization /
clustering.

```
capture/core   @vexa/mixed-capture-core   mixed audio + webrtc hook ─┐
capture/zoom   @vexa/zoom-capture          active-speaker hints       ├─ mixed-capture.v1 ─► pipeline
capture/teams  @vexa/teams-capture         active-speaker hints       ┘                       @vexa/mixed-pipeline
pipeline       @vexa/mixed-pipeline        segmenter(pyannote) + namer(hints) ─► transcript.v1
eval           agentic evaluation vehicle (YouTube fixture vs our pipeline)
```

Shared engine: `@vexa/transcribe-buffer` (LocalAgreement-2) + `@vexa/transcribe-whisper`
(stt.v1) + `@vexa/capture-codec` (wire). See each subfolder's README.
