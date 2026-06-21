# bot/scripts

[`check-isolation.js`](check-isolation.js) — the service's `gate:isolation` (P2) check: every
`src/` import must be intra-package, a Node builtin, or a declared dep (the composed bricks
`@vexa/{join, remote-browser, recording, record-chunker, gmeet-pipeline, mixed-pipeline,
transcribe-whisper}` + `ajv` / `ajv-formats` + devDeps) — never another brick's internals,
never another domain.

The orchestrator core (`config.ts · ports.ts · orchestrator.ts · contracts.ts`) imports only
its own files + node builtins + ajv; the `@vexa/*` front doors are touched solely at the
composition root (`index.ts`). Run by the gate as `node scripts/check-isolation.js`.
