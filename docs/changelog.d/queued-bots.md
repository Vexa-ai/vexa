- **A full cluster queues a new bot instead of failing it.** When no seat is free and the cluster
  cannot grow, a bot now waits with `status: queued` and a `status_reason` (`cluster_at_capacity`,
  then `will_join_late` once its meeting has started), on `GET /bots`, `GET /meetings` and the
  `meeting.status_change` webhook. It moves on to `joining` by itself when a seat frees. Queued bots
  count against the concurrent-bot limit; the `429` past it is unchanged. After 30 minutes without
  a seat the run ends `failed` with `completion_reason: cluster_at_capacity`, unretried and
  unbilled (no bot ran). See [Queued: waiting for a seat](/api/meetings#queued-waiting-for-a-seat).
