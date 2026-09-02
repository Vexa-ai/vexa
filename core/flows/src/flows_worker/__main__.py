"""flows-worker — THE production loop: claim → one step → receipts → advance, on durable
Postgres. Run N replicas freely (SKIP LOCKED). A step-duration watchdog enforces the no-sleep
law the live witness taught us."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, admit, escalate, postgres_db, reclaim, tick
from flows_defs import production
from flows_integrations import instance_gate
from flows_steps.common import db_url, require_internal_secret

POLL_S = 1.0
SLOW_STEP_S = 8.0


def main() -> int:
    # Refuse to start without the internal-tier secret, the same refusal flows-api already makes
    # for the operator key: a worker that starts without it runs every post-meeting turn with no
    # room and nothing says so until somebody reads a log.
    require_internal_secret()
    db = postgres_db(db_url())
    clock = SystemClock()
    reg = Registry()
    production.build(reg, db)

    def emit(event_type: str, source_id: str, refs: dict) -> int:
        return admit(db, reg, clock, source_event_id=source_id,
                     event_type=event_type, subject_refs=refs)

    print(f"flows-worker up · {len(reg.flows)} flows · {len(reg.steps)} steps", flush=True)
    last_recon = 0.0
    while True:
        if time.time() - last_recon > 10:
            last_recon = time.time()
            reclaim(db, clock)
            escalate(db, clock)
            fresh = reg.refresh_from_db(db)
            if fresh:
                print(f"hot-loaded {fresh} flow version(s) from the DB", flush=True)
        t0 = time.time()
        # The instance gate is injected HERE and nowhere else. `flows/` is the engine core and
        # `flows_integrations/` the adapters, so the loop must not learn what an instance is —
        # the process that composes them does. While the gate is up `tick` claims nothing, so
        # every fact admitted during setup keeps its place and runs, in order, once the admin
        # commits the company layer.
        worked = tick(db, reg, clock, emit=emit, gate=instance_gate.company_layer_ready)
        dt = time.time() - t0
        if dt > SLOW_STEP_S:
            print(f"⚠ SLOW STEP {dt:.1f}s — steps must never sleep (no-sleep law)", flush=True)
        if not worked:
            time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
