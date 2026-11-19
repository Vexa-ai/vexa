# blocks — the one renderer for `blocks.v1`

The same blocks draw in the new-chat home, in workspace pages (a ```vexa fence) and in agent chat
replies (#1783). `spec.ts` reads and caps them (`sanitizeHome`, `sanitizeFence`, `parseVexaFence`);
`Blocks.tsx` draws them (`BlocksView`, `CardView`, `VexaFence`) and holds the progressive-disclosure
pieces every surface shares: `Peek` (an anchored panel with summary, properties and the target's own
blocks; Open; Esc or × folds; a bottom sheet on a phone), collapse to one line, show more, and Expand
in place. What a button does is the host's, through `BlocksProvider`; `blocks.css` is imported by
`../styles.css`. Contract: `core/agent/contracts/blocks.v1`.
