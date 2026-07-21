- **meeting-api RSS no longer climbs under production callback traffic (#803).** The in-process
  webhook-envelope capture (`app.state.status_change_webhooks` / `typed_webhooks`) — an eval seam
  that lived on the production app — was an unbounded list: every bot lifecycle callback appended an
  envelope embedding the meeting projection, so RSS grew monotonically under real callback traffic
  while idle staging (no callbacks) stayed flat. It is now a bounded ring buffer, capping retention
  while preserving the recent-envelope semantics the eval relies on. Operators on the inherited 1Gi
  limit no longer see the slow OOM cycle from this source.
