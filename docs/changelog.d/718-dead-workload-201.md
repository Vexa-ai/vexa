- **A bot that fails to start now surfaces the failure instead of a false success (#718).** When the
  runtime cannot start a bot workload (e.g. the bot image is absent), `POST /bots` returns `502` with
  the reason (the missing image is named) and the meeting is marked `failed` on the spot — no more a
  `201` over a workload that never came up, followed by five silent minutes and a reason-less
  `failed`. The runtime kernel answers the spawn honestly (non-201 + `workload_spawn_failed`), the
  meeting-api refuses the dead spawn at both the runtime seam and the service, and `make dev` now
  exits non-zero naming an unpulled bot image even when an earlier build step fails.
