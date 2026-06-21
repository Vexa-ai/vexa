# mixed-capture-core/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/mixed-capture-core` is page code with ZERO external imports (only declared devDeps) —
never another brick's internals, never node/Playwright. (DOM globals like `AudioContext` /
`RTCPeerConnection` are ambient, not imports, so they're not scanned.)
