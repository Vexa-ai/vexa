# transcript-viewer — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Presentational React transcript view for the modular dashboard. Props in, DOM out: renders attributed segments (speaker + text + time), a live indicator, and a search box over an injected TranscriptSegment[] (typed by @vexa/dash-contracts). No store, no fetch, no ws — the clean counterpart of the vendored dashboard's transcript-viewer with the coupling stripped. Inline-styled so it paints identically in a bare browser fixture and on the real stack.

