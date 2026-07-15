"""Cross-replica sweep coordination for the lifespan background loops.

At ``meetingApi.replicaCount>1`` (the Helm/hosted default) every meeting-api process starts the
SAME set of background loops. Without a guard each sweep's real work runs once *per replica* instead
of once *per interval* — most sharply the ``calendar-sync`` external ICS fetch, which re-hits every
user's third-party calendar provider on each replica. ``single_flight`` makes that structural: a
per-loop Postgres advisory lock elects one runner per tick.
"""
