# flows_worker

THE production loop (`python -m flows_worker`): claim → one step → receipts → advance on durable
Postgres; N replicas cooperate via SKIP LOCKED. Step-duration watchdog enforces the no-sleep law.
