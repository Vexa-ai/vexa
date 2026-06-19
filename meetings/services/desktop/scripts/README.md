# desktop/scripts

[`check-isolation.js`](check-isolation.js) — the service's `gate:isolation` (P2) check: every
`src/` import must be intra-package, a Node builtin, or a declared dep (the composed bricks
`@vexa/*` + `ws` + devDeps) — never another brick's internals.

[`observe.mjs`](observe.mjs) — the **live transcript-dynamics harness**. Taps the running
desktop's gateway `/ws` (the same stream the extension sidepanel renders) and prints what a
viewer actually experiences: per segment, `forming → churn → confirm` with audio-time span +
gap, the **warm-up** (time to first confirm), the **oversegmentation** signal (% of ≤3-word
segments), and a **lost-transcript monitor** (`⚠ LOST` = pending shown then cleared without
confirming). Records the raw stream to `/tmp/transcript-rec-*.jsonl` for offline replay.

Run from the repo root while a session is live (it resolves `ws` from this package):

```bash
pnpm observe                       # watch ALL sessions
pnpm observe youtube 53yPfrqbpkE   # watch one
```

Set `VEXA_SEG_DEBUG=1` on the desktop process to also log pyannote's split boundaries +
class context. The live companion to the offline `@vexa/eval` (launch/drive/judge).
