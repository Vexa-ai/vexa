# telemost-capture/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/telemost-capture` is page code with ZERO external imports (only declared devDeps) —
never another brick's internals, never node/Playwright. (DOM globals like `document` are
ambient, not imports, so they're not scanned.)
