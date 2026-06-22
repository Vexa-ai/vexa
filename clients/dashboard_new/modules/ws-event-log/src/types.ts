/**
 * types.ts — the props vocabulary, grounded on @vexa/dash-contracts.
 *
 * This brick renders pre-summarized rows, so its data shape is tiny. But the `type` field is not a
 * free-for-all string — it names a `ws.v1` frame. We pull `WsFrame` from the contracts brick and derive
 * the tag union (`WsFrameType`) from it, so the rows we render are anchored to the SAME sealed WS
 * vocabulary @vexa/dash-ws fans out. The import is TYPE-ONLY (erased at compile), so the presentational
 * brick carries zero runtime dependency on the contracts brick — it only borrows the WS frame shape.
 *
 * `WsLogEvent.type` is `WsFrameType | (string & {})`: the modeled tags get autocomplete + checking, yet
 * an additive/forwarded tag the dashboard doesn't model (e.g. "transcript.mutable", "connect") still
 * type-checks — the gateway forwards the raw redis payload verbatim, so the producer can add tags.
 */
import type { WsFrame } from "@vexa/dash-contracts";

/** The set of `type` tags @vexa/dash-contracts models on the `/ws` multiplex. */
export type WsFrameType = WsFrame["type"];

/** One row in the debug log: a single WS frame, already summarized for display. */
export interface WsLogEvent {
  /** Human-readable timestamp for the row (e.g. "10:00:05"). Optional — omit to hide the column. */
  ts?: string;
  /** The frame's `type` tag — a modeled `WsFrameType`, or any forwarded/additive tag string. */
  type: WsFrameType | (string & {});
  /** A one-line human summary of the frame body (e.g. `status: active`, `Alice: hi there`). */
  summary: string;
}
