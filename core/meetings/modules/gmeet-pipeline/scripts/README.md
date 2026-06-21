# gmeet-pipeline/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/gmeet-pipeline` may import only the shared engine
(`@vexa/transcribe-{buffer,whisper}`) and declared devDeps — never another brick's
internals.
