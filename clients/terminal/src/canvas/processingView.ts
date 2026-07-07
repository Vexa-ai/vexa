/** Pure meeting view-mode decision — kept OUT of the React component so it is testable in isolation
 *  (no JSX in the import graph). [N8] RELOAD-EQUIVALENCE. */

/** Default view when the user hasn't touched the toggle:
 *  - LIVE meeting → raw (unchanged): processing is an explicit opt-in that arms the copilot.
 *  - COMPLETED meeting → processed IF durable notes were persisted (they're the meeting's real
 *    output; defaulting to raw made users think their processed notes were lost), else raw. */
export function defaultProcessingView(live: boolean, hasNotes: boolean): boolean {
  return !live && hasNotes;
}

/** The view-mode decision. `live` comes from the meetings-list `session_uid`, which can stay STALE
 *  after a stop (the list row is stuck at active/stopping) — that left the pane on Raw until a reload.
 *  Durable truth wins ([N8]): once the durable row is TERMINAL, the meeting is not effectively-live,
 *  so a completed meeting with notes shows Processed regardless of the stale live flag. */
export function deriveProcessingView(o: {
  override: boolean | null;
  live: boolean;
  hasNotes: boolean;
  durableTerminal: boolean;
}): boolean {
  if (o.override !== null) return o.override;
  return defaultProcessingView(o.live && !o.durableTerminal, o.hasNotes);
}
