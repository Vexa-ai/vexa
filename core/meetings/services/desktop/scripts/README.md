# desktop/scripts

[`check-isolation.js`](check-isolation.js) — the service's `gate:isolation` (P2) check: every
`src/` import must be intra-package, a Node builtin, or a declared dep (the composed bricks
`@vexa/*` + `ws` + devDeps) — never another brick's internals.

> The live transcript-dynamics harness moved to `@vexa/eval` — run `pnpm observe` (see
> `meetings/eval/src/observe.mjs`). Set `VEXA_SEG_DEBUG=1` on this desktop process to also
> log pyannote's split boundaries + class context.
