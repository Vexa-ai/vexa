- **The live WebSocket will honour key scopes (#1784), from 0.13.6.** In 0.13.4, as in 0.12, any
  valid key opens `/ws` and subscribes to the meetings it may read; a key without `tx` is logged
  (`ws_scope_not_enforced`). From 0.13.6 subscribing to a meeting's live transcript takes the same `tx`
  scope as `GET /transcripts/{platform}/{native_meeting_id}`. Give live-transcript callers a `tx` key.
