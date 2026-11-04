- **Bot starts are paced, and a stopped bot keeps its seat for a minute.** An account may start 10
  bots at once and one every 2 minutes after that, however it starts them (`POST /bots` answers
  `429`, `reason: bot_start_rate`, with `Retry-After`; a calendar auto-join that hits it is retried
  later). Both limits are defaults that a deployment can set per plan and per account. A bot the user
  stopped holds its place in the concurrent-bot limit for 60 seconds after it ends, so stopping and
  restarting cannot cycle bots through one seat. Booked bots are exempt. See
  [Meetings](/api/meetings).
