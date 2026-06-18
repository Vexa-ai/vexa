# capture-codec/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2)
check: every `src/` import must be intra-package, a Node builtin, or a declared
dep. capture-codec is pure/zero-dep, so it must import nothing.
