# flows_worker

THE production loop (`python -m flows_worker`): claim → one step → receipts → advance on durable
Postgres; N replicas cooperate via SKIP LOCKED. Step-duration watchdog enforces the no-sleep law.

`unit.py` traces it (logevent.v1): one trace per tick, bound while the step runs and forwarded as
`X-Trace-Id` on its Vexa calls; the registry's steps are wrapped to log `step_dispatched` /
`step_outcome` / `step_failed` with the reaction id — never refs or error text.
