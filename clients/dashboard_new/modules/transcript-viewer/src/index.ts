/**
 * src/index.ts — the ONE front door of @vexa/dash-transcript-viewer.
 *
 * Re-exports the presentational TranscriptViewer component and its props type. Nothing else is public.
 * The component is typed by @vexa/dash-contracts (`TranscriptSegment`) and is pure props-in/DOM-out:
 * no store, no fetch, no ws — data is injected by the caller.
 */
export { TranscriptViewer } from "./TranscriptViewer.js";
export type { TranscriptViewerProps } from "./TranscriptViewer.js";
