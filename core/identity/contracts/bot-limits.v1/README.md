# bot-limits.v1 — an account's resolved bot limits

The one carrier of an account's bot limits from identity to meetings: the `bot_limits` field of
admin-api's internal `GET /internal/users/{id}/bot-context` (internal tier, `X-Internal-Secret`).
meeting-api reads it on every path that starts a bot (`POST /bots` from the gateway or the Flows
door, the calendar auto-join, a continued meeting) and enforces it in one place, the guarded create
under the per-user lock. meeting-api reads no identity table and no `X-User-Limits` header.

- **`BotLimits`** — `version` (`1`), `max_concurrent_bots` (always present: the account's
  `users.max_concurrent_bots` column; `0` for the instance admin account), and any of
  `bot_start_rate`, `seat_cooldown_s`, `queue_wait_s`, `max_scheduled_bots`, `booking_rate`. A key
  identity has no value for is absent; meetings then applies its built-in default. Further limits
  are further keys of the same value shapes (`Limit`): the carrier does not change.
- **Resolution** (identity's side, per key except the cap): the account's `users.data["bot_limits"]`,
  else its plan's (`platform_settings` `bot_limits.plans[<users.data.subscription_tier>]`), else the
  platform default (`bot_limits.default`). No migration: existing column, existing JSON columns.
- **Version.** A reader that gets no `bot_limits`, or a `version` it does not know, from a configured
  identity refuses the start (`503`) rather than run unlimited.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
