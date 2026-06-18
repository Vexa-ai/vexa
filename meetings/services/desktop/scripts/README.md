# desktop/scripts

[`check-isolation.js`](check-isolation.js) — the service's `gate:isolation` (P2) check: every
`src/` import must be intra-package, a Node builtin, or a declared dep (the composed bricks
`@vexa/*` + `ws` + devDeps) — never another brick's internals.
