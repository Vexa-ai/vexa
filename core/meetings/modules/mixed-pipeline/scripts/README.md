# mixed-pipeline/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/mixed-pipeline` may import only the shared engine
(`@vexa/transcribe-{buffer,whisper}`), the pyannote runtime
(`@huggingface/transformers`, `onnxruntime-node`), and declared devDeps — never
another brick's internals.
