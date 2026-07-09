# record-chunker/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/record-chunker` is pure browser code with ZERO external imports (only declared devDeps) —
never another brick's internals, never node/Playwright. (Browser globals like `MediaRecorder` /
`AudioContext` / `btoa` are ambient, not imports, so they're not scanned.)
