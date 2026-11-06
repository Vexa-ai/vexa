# meeting_api.admission — per-account bot limits (shadow)

Each account gets two limits, `scheduled` and `adhoc`, from its own peak concurrent bots by hour of
the week, plus 50% rounded up. A new account gets 2 and 2. A cap raised above the plan default of 5 is
a floor. This module only **computes and logs**: it changes no API answer and no spawn.

`BOT_ADMISSION_MODE` selects `off` (the default, and any value other than `shadow`) or `shadow`. Off
means no observer, no loop and no route, so nothing is queried, written or logged.

* `predict` — the arithmetic: pure functions, no I/O.
* `sweep` — the daily job, single-flighted. It reads `user_id` and timestamps from `meetings`, never
  `meetings.data`, and stores predictions in Redis for 3 days. A row started more than 30 minutes
  after it was created counts as booked, and stays out of the ad-hoc limit.
* `shadow` — runs in `request_bot` before the existing guard, under a short timeout and unable to
  raise. It logs `bot_admission_shadow`, and `bot_admission_outside_prediction` at most once per
  account per 7 days. It also keeps the booked peaks the scheduled limit learns from.
* `router` — `GET /internal/bot-admission/reading`, internal tier only, mounted only in shadow.
