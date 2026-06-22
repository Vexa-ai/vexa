/**
 * @vexa/dash-ws-event-log — the WS frame-log debug VIEW (the single front door).
 *
 * A presentational React component: `{ events: WsLogEvent[] }` in, a terminal-style live frame log out
 * (one row per frame, NEWEST FIRST, showing type + summary). It owns no store, no fetch, no socket — the
 * rows are injected by whoever owns the live WS (e.g. @vexa/dash-ws / @vexa/dash-meeting-state). Typed
 * against the @vexa/dash-contracts WS vocabulary.
 *
 * Everything the brick exposes is reachable through THIS file (the `.` export) — the component and its
 * props/row types. Nothing else is public.
 */
export { WsEventLog } from "./WsEventLog.js";
export type { WsEventLogProps } from "./WsEventLog.js";
export type { WsLogEvent, WsFrameType } from "./types.js";
