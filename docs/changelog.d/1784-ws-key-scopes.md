- **The live WebSocket honours key scopes (#1784).** `/ws` now applies the REST routes' scopes: a key
  that cannot read meeting status (`GET /meetings`, `GET /bots/status`) is answered `insufficient_scope`
  and closed with `4403`, and subscribing to a meeting's live transcript takes the same `tx` scope as
  `GET /transcripts/{platform}/{native_meeting_id}`; otherwise `insufficient_scope`, and the socket
  stays open for status frames.
