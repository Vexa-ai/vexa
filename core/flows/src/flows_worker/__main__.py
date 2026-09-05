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
import flows_config
from flows_steps import common
from flows_steps.common import db_url, require_internal_secret

POLL_S = 1.0
SLOW_STEP_S = 8.0


def main() -> int:
    # Refuse to start without the internal-tier secret, the same refusal flows-api already makes
    # for the operator key: a worker that starts without it runs every post-meeting turn with no
    # room and nothing says so until somebody reads a log.
    require_internal_secret()
    # …and without a door it cannot name. There are no host-port defaults: `http://localhost:18057`
    # is a DIFFERENT DEPLOYMENT'S admin-api on any host running two stacks, so an unnamed door that
    # falls back to one is worse than a refusal — it works, against the wrong thing. The agent door
    # is exempt by construction: unset means the agent domain is not deployed (decision 40.7).
    flows_config.preflight()
    # …and the CONTRACT's own check, which is a different question (E6/ADR-0026): every
    # required-explicit key in `config.v1.json` is set, and no key still holds one of the
    # placeholder literals the declaration forbids — literals that are published in this
    # repository and are therefore not secrets. The vendored validator is the same file every
    # adopted service runs; flows imported it nowhere until now, which is how a hand-copied list
    # of four placeholders in `flows_api.py` came to stand in for the declaration's seven.
    from config_preflight import preflight as contract_preflight
    contract_preflight()
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
        # …and the DOMAIN PRESENCE predicate is injected in the same place and for the same
        # reason (PRD decision 40.7): which domains a deployment runs is a fact about this
        # process's configuration, not about the engine. A step that names an absent domain
        # answers `not_present` — terminal, with the reason on the reaction — instead of
        # retrying against a door that is not there.
        worked = tick(db, reg, clock, emit=emit, gate=instance_gate.company_layer_ready,
                      present=common.domain_present)
        dt = time.time() - t0
        if dt > SLOW_STEP_S:
            print(f"⚠ SLOW STEP {dt:.1f}s — steps must never sleep (no-sleep law)", flush=True)
        if not worked:
            time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
