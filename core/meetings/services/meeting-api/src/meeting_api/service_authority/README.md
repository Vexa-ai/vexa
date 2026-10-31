# service_authority

The meeting-api-owned, policy-free seam for asking an independently deployed
authority whether one bot session may be admitted or continue through a
one-minute service boundary.

Public surface: `meeting_api.service_authority`.

The module may depend on Python's standard library, `httpx`, the published
`service-authority.v1` wire contract, and injected meeting/runtime ports. It
must not know about payment providers, prices, balances, customer records, or
hosted plan policy. With no authority configured it uses the explicit
allow-all adapter, preserving the OSS self-hosted behavior.

## Unavailable authority

`failure_policy=closed` means **admission only**: `POST /bots` answers 503
`service_authority_unavailable`. At a continuation boundary (`sweep.py`) an
unavailable authority fails open in `enforce` and `observe` mode: no decision
is recorded, `last_boundary_at` stays put, the minute is booked in
`data.service_authority.debt_boundaries` (`record_service_authority_debt`),
`service_authority_fail_open meeting_id=… boundary=… reason=unavailable` is
logged once per minute, and the bot keeps running. The first unavailable
answer opens a per-tick circuit: no further authority call that tick.
Recovery replays each debt minute in order with the same `request_id` and the
stored `active_concurrency`; each decision settles its entry once
(`debt_settled_count`, `debt_open`). An enforced deny still stops the bot.
Debt outlives the session: every sweep also lists rows in any status whose
`data @> {"service_authority": {"debt_open": true}}`
(`list_service_authority_debt_sessions`) and replays their remaining debt
minutes, because the bot did run through them. A row stopped by a deny this
tick is replayed from the next tick, once its teardown is confirmed (a pending
stop is never overwritten by a later allow); such a row books no new minute,
and a deny on one of its minutes is recorded with
`teardown_skipped=meeting_not_live` and stops nothing. So each debt minute is
settled exactly once, provided the authority eventually answers it.
