- **Booked meetings: a booking cap, a strict time format, and the start for one planned row (#1962).**
  `POST /meetings` now refuses a `scheduled_at` that is not ISO-8601 (422) and, once an account has
  500 future meetings booked with a bot (`BOOKED_MAX_PER_ACCOUNT`), answers 429 with the cap. The
  same holds when an existing plan becomes a booking with a bot: `PATCH /meetings/{id}` giving it a
  time or turning `auto_join` back on, and `PUT /meetings/{platform}/{id}/intent`; moving or editing
  a meeting that is already booked is never refused. Calendar sync still mirrors events over the
  cap, with no bot and the reason on the meeting (`auto_join_error`). A bot that asked for help
  (`needs_help`) now counts against the concurrency limit. Internally, every change to a planned
  meeting is now handed to Flows durably, and Flows can start the bot for that exact meeting; the
  existing 2-minute auto-join stays on until the next step.
