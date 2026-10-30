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
**Limit (next slice):** debt of a meeting stopped by that deny, or ended
during the outage, keeps `debt_open: true` and is not replayed yet.
