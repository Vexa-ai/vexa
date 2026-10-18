# flows — the engine

Stdlib-pure at import (check-isolation enforced): the leased worker loop (`loop.py` — claim one
due reaction, run one step between two commit points, advance), admission-by-constraint, receipts,
the two-UPDATE reconciler, signal verbs, and the status projection. Knows Postgres (and only Postgres, 2026-09-03) through the `db.py` seam and NOTHING else — no
meetings, no agent, no runtime, no scheduler: time is the `next_run_at` column. Import from `flows` (the front door), never a deep path.

Time, kept in Postgres: `schedule.py` fires cron slots (a one-shot `at` schedule fires once and
retires its version); `triggers.py` reads a trigger's event glob, its closed filter grammar and a
relative time (`payload.start - 60s`); `keyed.py` keeps one pending fire per key for a relative
trigger (an update moves it, a cancel removes it) and fires due rows into ordinary reactions.

`fact_envelope.py` is event.v2's sealed fact envelope (D8), vendored verbatim from
`core/flows/contracts/event.v2/` (parity fact `fact-envelope`): the intake verifies a producer's
envelope with it, and `admission.py` admits a person's flow by subject ∪ the verified audience.
