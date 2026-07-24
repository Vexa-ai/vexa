# mixed-pipeline/src

Front door [`index.ts`](index.ts). [`chunked-transcriber.ts`](chunked-transcriber.ts)
is the single-channel core: a passive audio ring, segmentation-cut turns, the
serialized submit queue, and continuous LocalAgreement confirmation over the shared
[`buffer`](../../buffer/)/[`whisper`](../../whisper/) engine.
[`pyannote-segmenter.ts`](pyannote-segmenter.ts) is the cut source — a streaming
wrapper around `onnx-community/pyannote-segmentation-3.0` (the only ONNX; cut-only, no
clustering). [`cluster-name-binder.ts`](cluster-name-binder.ts) is the hints-only
namer: it window-matches Zoom/Teams evidence and binds Jitsi's exclusive trailing
state through ordered transition custody. No diarization.

`*.test.ts` are the offline, model-free goldens (`gate:node` runs them via the `test`
script): the confirm-loop characterization, naming / claim / priority / concurrency /
flicker smokes, and M1/M2 causal replay contracts. Each injects its own segmenter and
a stub Whisper, so the ONNX model never loads and there is no network.
