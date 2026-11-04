- **Ad-hoc bot starts are paced, and a stopped bot keeps its seat for a minute.** `POST /bots`
  allows an account 10 starts at once and one every 2 minutes after that (`429`,
  `reason: bot_start_rate`, with `Retry-After`); scheduled starts are not counted. A bot the user
  stopped holds its place in the concurrent-bot limit for 60 seconds after it ends, so stopping and
  restarting cannot cycle bots through one seat. Booked bots are exempt. See
  [Meetings](/api/meetings).
