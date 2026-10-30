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

`failure_policy=closed` now means **admission only**. An authority that times
out, answers non-2xx or malformed:

- **Admission** (`action=admit`) fails closed: `ServiceAuthorityUnavailable`,
  which the bot router maps to 503 `service_authority_unavailable`.
- **Continuation** (`action=continue`, `sweep.py`) fails open. No decision is
  recorded and `last_boundary_at` does not advance; the minute is booked as
  debt in `meeting.data.service_authority.debt_boundaries` through the
  `record_service_authority_debt` repo port, and the sweep logs
  `service_authority_fail_open meeting_id=… boundary=… reason=unavailable`
  once per minute. The bot keeps running in both `enforce` and `observe`
  mode. One `ServiceAuthorityUnavailable` opens a per-tick circuit: the sweep
  books the remaining due minutes of every live session as debt without
  another authority call.
- **Recovery** replays each debt minute in order with the same `request_id`
  (`<service_identity>:continue:<boundary>`) and the stored
  `active_concurrency`. A recorded decision settles its debt entry on the row:
  the entry leaves `debt_boundaries` (`[]` once empty),
  `debt_settled_count` is incremented, and `debt_open` turns false once no
  entry remains (`debt_open` is true from the first booked minute). An
  enforced deny on a replayed minute stops the bot like any other enforced
  deny.
- **Stopped or ended meetings** keep `debt_open: true` until settled. Every
  sweep also lists rows in any status whose
  `data @> {"service_authority": {"debt_open": true}}`
  (`list_service_authority_debt_sessions`) and replays their remaining debt
  minutes, because the bot did run through them. A row stopped by a deny in
  this tick is replayed from the next tick. A deny on such a minute settles
  it without a stop, since the row is no longer live. So every debt minute is
  settled exactly once, provided the authority eventually answers it.
- The sweep counts one fault per tick for an outage, not one per open debt
  minute.
