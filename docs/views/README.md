# architecture views (generated)

Deterministic perspectives of [`architecture.calm.json`](../../architecture.calm.json), rendered by
[`scripts/arch-viz.mjs`](../../scripts/arch-viz.mjs). **The `.svg` files here are generated and gitignored
— do not edit them by hand.** Regenerate:

```
pnpm arch:viz                       # list selectors
pnpm arch:viz cluster:meetings      # a concern bundle + the carriers it touches
pnpm arch:viz flow:transcript-flow  # a data path, contract on each hop
pnpm arch:viz path:proc-stream      # a carrier: its writers → readers
pnpm arch:viz all                   # every cluster + flow
```

Flags: `--lod=0..3` (detail), `--scale=0.5..2.5`, `--no-contracts`, `--no-owners`. Same model + same
spec ⇒ byte-identical output. The model is kept true by `gate:dataflow` (P23); see `docs/ARCHITECTURE.md`.
