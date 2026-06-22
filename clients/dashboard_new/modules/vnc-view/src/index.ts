/**
 * @vexa/dash-vnc-view — the single front door.
 *
 * One presentational React VIEW: the #5 per-bot noVNC viewer. Props in, DOM out — no store, no fetch,
 * no ws. The caller composes the per-bot `/b/{id}/vnc/...` URL (routed by the gateway later) and injects
 * it as `vncUrl`; an empty `vncUrl` renders the loading placeholder.
 */
export { VncView, default } from "./VncView.js";
export type { VncViewProps } from "./VncView.js";
