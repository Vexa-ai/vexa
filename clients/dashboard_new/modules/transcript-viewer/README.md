# @vexa/dash-transcript-viewer — presentational transcript view

_dashboard_new/ · view brick · `TranscriptSegment[]` in → attributed-transcript DOM out._

A pure React component that renders a meeting transcript: **attributed segments** (speaker + text +
time), a **live indicator**, and a **search box**. Props in, DOM out — **no store, no fetch, no ws**.
The caller injects the data and the click handler; the component owns only its local search-query state.

This is the clean modular counterpart of the vendored dashboard's
`components/transcript/transcript-viewer.tsx` (+ `transcript-segment.tsx`), with the coupling stripped:
no API client, no export library, no cookies, no AI panel, no shadcn/Tailwind component imports, no
auto-scroll side effects. It keeps exactly what a reader sees. Typed entirely by
[`@vexa/dash-contracts`](../dash-contracts/) and inline-styled, so it paints identically in a bare
browser fixture and on the real stack with zero global-CSS dependency.

## Props contract

```ts
import type { TranscriptSegment } from "@vexa/dash-contracts";

interface TranscriptViewerProps {
  segments: TranscriptSegment[];                 // required — rendered in feed order
  isLive?: boolean;                              // default false — shows the pulsing "Live" badge
  playbackTime?: number;                         // seconds — marks the segment whose [start,end] window
                                                 //   contains it as active (left accent bar). No scroll.
  onSegmentClick?: (                             // when provided, segments become clickable
    startTimeSeconds: number,
    absoluteStartTime?: string,
  ) => void;
}
```

- `segments` — each shaped per dash-contracts `TranscriptSegment` (all fields optional; `text`,
  `speaker`, `start_time`/`end_time`, `absolute_start_time`, `completed`, `segment_id` are the ones
  read). Time shown is `absolute_start_time` (HH:MM:SS, viewer-local tz) when present, else `start_time`
  as mm:ss. A segment with `completed === false` renders muted/italic (pending).
- Distinct speakers are colored by order of first appearance (stable palette).
- **Search** is local component state: filters segments whose text OR speaker contains the query
  (case-insensitive) and `<mark>`-highlights matches.
- The component renders no scrollbars-driven side effects and reads no globals beyond `Date` for time
  formatting — it is deterministic for the given props.

### DOM contract (data-testids)
`transcript-viewer` · `transcript-search` · `transcript-body` · `transcript-empty` ·
`transcript-segment` (one per rendered segment, carries `data-speaker`) · `segment-speaker` ·
`segment-time` · `segment-text` · `live-indicator` (only when `isLive`).

## Surface
`TranscriptViewer` (+ type `TranscriptViewerProps`). Front door: [`src/index.ts`](src/index.ts).

## Verify
`npm run build` — `tsc` typechecks the component against dash-contracts (jsx + DOM lib).

The L4 gate is a **real-browser** mount: `npm test` (Playwright, chromium) esbuild-bundles the REAL
component + a fixture page ([`e2e/`](e2e/)), serves it over a stdlib static server, mounts
`<TranscriptViewer/>` over two golden segments (speakers **"Anna"**, **"Zoya"**), and asserts the
rendered DOM shows **both speakers + both texts**, the live indicator, and that the search box filters
the rows. green-in-Playwright ⇒ green-for-the-human's-browser. (`npm run install:browser` once if
chromium isn't installed.)
