- **Google Meet: Stop now leaves the lobby while the bot is still `awaiting_admission` (#889).** A
  Stop issued while a bot was knocking in the waiting room (never admitted) did not withdraw it — the
  bot kept showing as a pending "asking to join" attendee. The stop is delivered as a `leave` command
  on the bot's channel, but the orchestrator subscribed to that channel only *after* admission, so a
  lobby-phase `leave` was dropped (Redis pub/sub has no backlog) and `handle()` routed `leave` to the
  active-phase end rather than the pre-active abort. The orchestrator now subscribes before the join
  race and routes a `leave` through the same phase-aware stop path, so a Stop during
  `awaiting_admission` cancels the join request and closes the lobby tab.
