# meeting_api.sweeps — single-flight guard for background sweeps (#637)

At `meetingApi.replicaCount > 1` every meeting-api replica starts the same background loops, so each
sweep's real work runs once **per replica** instead of once per interval. This package makes that
safety structural rather than accidental.

`single_flight` wraps a sweep tick in a Postgres **session-level advisory lock** keyed by loop name
(a fixed `classid` disjoint from the per-user `pg_advisory_xact_lock` keyspace): the replica that
acquires the lock runs the tick, the others skip it that interval. A replica that dies mid-tick drops
its session lock on disconnect, so the next interval is picked up elsewhere — no leader-election infra.

The guard **degrades to run-the-tick** when no DB session factory is available (single-replica / Lite),
so single-replica behaviour is unchanged. The `segment-consumer` loop is deliberately **not** wrapped —
it is already single-delivery via the Redis consumer group, and wrapping it would serialize the
replicas' stream reads.
