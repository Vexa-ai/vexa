- **Follow a live transcript without re-downloading it — `GET /transcripts/…?since=` (#1219).** The
  transcript endpoint returned the whole transcript on every poll, so following one 2-hour meeting at
  a 5s poll cost ~172MB to learn ~250KB of new text. Pass `since` (ISO-8601 UTC) to get back only the
  segments created or changed since then; every response now carries `next_since` to pass to your next
  poll, plus `retracted_segment_ids` for segments that were withdrawn. Merge by `segment_id` — a live
  draft is rewritten in place as its confirmation, so a segment you already hold comes back when it
  changes. See [Following a live meeting](/api/meetings#following-a-live-meeting-since).
- **Unsupported query parameters on `/transcripts/*` are refused, not silently dropped (#1219).**
  `?limit=2`, `?after=…`, `?offset=…` and `?start_time=…` used to return `200` with the full payload —
  the caller was told the request succeeded and handed everything anyway. They now return `400` naming
  the parameter and listing what the endpoint accepts. See [Errors](/api/errors).
