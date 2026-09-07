# flows — the engine

Stdlib-pure at import (check-isolation enforced): the leased worker loop (`loop.py` — claim one
due reaction, run one step between two commit points, advance), admission-by-constraint, receipts,
the two-UPDATE reconciler, signal verbs, and the status projection. Knows Postgres (and only Postgres, 2026-09-03) through the `db.py` seam and NOTHING else — no
meetings, no agent, no runtime, no scheduler: time is the `next_run_at` column. Import from `flows` (the front door), never a deep path.
