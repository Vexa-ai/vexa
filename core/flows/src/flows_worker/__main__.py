"""flows-worker — THE production loop: claim → one step → receipts → advance, on durable
Postgres. Run N replicas freely (SKIP LOCKED). A step-duration watchdog enforces the no-sleep
law the live witness taught us."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, admit, escalate, postgres_db, reclaim
from flows.schedule import fire_due
from flows import keyed
from flows.admission import admissible
from flows_defs import production
import flows_config
from flows_steps import policies
import flows_obs
from flows_steps import common
from flows_worker.unit import SERVICE, instrument, run_unit
from flows_steps.common import db_url, require_internal_secret

POLL_S = 1.0
SLOW_STEP_S = 8.0


def admission_limits() -> dict:
    """The loop guard and the per-person daily cap, from `config.v1` (ADR-0046 decisions 8, 9)."""
    return {"loop_limit": flows_config.get_int("VEXA_FLOWS_LOOP_LIMIT"),
            "max_hops": flows_config.get_int("VEXA_FLOWS_MAX_HOPS"),
            # live from `_global/POLICIES.md` (`routine_runs_per_day`), the env as the install default
            "runs_per_day": policies.company_quota("routine_runs_per_day", "VEXA_FLOWS_USER_RUNS_PER_DAY"),
            "messages_per_day": flows_config.get_int("VEXA_FLOWS_USER_MESSAGES_PER_DAY")}


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
    # COMPANY FLOWS ARE THE GLOBAL ACCOUNT'S (seeds, written once, never overwritten).
    seeded = production.seed(db)
    if seeded:
        print(f"seeded company flow(s): {', '.join(seeded)}", flush=True)
    reg.refresh_from_db(db)
    # logevent.v1: one trace per unit of work (one tick = at most one step), forwarded as
    # X-Trace-Id on every Vexa call the step makes; see flows_worker/unit.py.
    flows_obs.set_service(SERVICE)
    instrument(reg)

    def emit(event_type: str, source_id: str, refs: dict) -> int:
        n = admit(db, reg, clock, source_event_id=source_id,
                  event_type=event_type, subject_refs=refs, **admission_limits())
        # A ROOM'S PLANNED CALL FOLLOWS ITS CREATOR'S CALENDAR (PLAN-8H 8.4): a moved or cancelled
        # event is handed to agent-api's room door as its owner. Never stops the pass.
        if event_type.startswith("calendar.event.") and common.domain_present("agent"):
            from flows_sensors import planned_sync as _planned_sync
            _planned_sync.on_change(event_type, refs)
        return n

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
            # FLOWS OWN TIME (ADR-0046 decision 3): a due schedule slot becomes a reaction here.
            fired = fire_due(db, reg, clock,
                             catchup_s=float(flows_config.get_int("VEXA_FLOWS_SCHEDULE_CATCHUP_S")),
                             **admission_limits())
            if fired:
                print(f"fired {fired} schedule slot(s)", flush=True)
            # KEYED SCHEDULES (ADR-0050): one pending fire per key — the bot one minute before a
            # meeting, moved by every update, called off by a cancel — fired here like a slot.
            try:
                kfired = keyed.fire_due(db, reg, clock, admissible=lambda f, refs: admissible(
                    db, f, clock, event_type=keyed.KEYED_EVENT, subject_refs=refs, **limits))
                if kfired:
                    print(f"fired {kfired} keyed schedule(s)", flush=True)
            except Exception as exc:  # noqa: BLE001 — a keyed pass never stops the loop
                print(f"keyed pass failed: {type(exc).__name__}", flush=True)
            # SENSORS (ADR-0050 decision 6): every enabled sensor whose check is due reads its
            # provider's changes through the broker, filters them and admits them as events.
            try:
                from flows_sensors import pool as _sensors
                from flows_sensors.calendar import KINDS as _kinds
                ticked = _sensors.run_due(db, _kinds, emit)
                if ticked:
                    print(f"ticked {len(ticked)} sensor(s)", flush=True)
            except Exception as exc:  # noqa: BLE001 — a sensor pass never stops the loop
                print(f"sensor pass failed: {type(exc).__name__}", flush=True)
            # THE DELIVERY GATE (PRD 0.13.4 D15, S6): in-app first, one email after the idle window,
            # then the digest — stopping the moment crm says the item is seen. Off unless declared.
            if flows_config.get_bool("VEXA_FLOWS_NUDGES"):
                from flows_delivery import run as _delivery
                done = _delivery.tick(db, clock)
                if any(v for k, v in done.items() if k in ("notified", "emailed", "digest_digests")):
                    print(f"delivery pass: {done}", flush=True)
            # …and a fire the person's daily cap refused says so in the routine's chat, once a day.
            if common.domain_present("agent"):
                from flows_steps import routine as _routine
                try:
                    _routine.announce_refusals(db)
                except Exception as exc:  # noqa: BLE001 — a notice never stops the loop
                    print(f"refusal notice not posted: {type(exc).__name__}", flush=True)
        t0 = time.time()
        # The DOMAIN PRESENCE predicate is injected HERE and nowhere else (PRD decision 40.7):
        # which domains a deployment runs is a fact about this process's configuration, not about
        # the engine. A step that names an absent domain answers `not_present` — terminal, with the
        # reason on the reaction — instead of retrying against a door that is not there.
        # There is no instance gate: nothing waits for a company layer (founder ruling 2026-10-08).
        worked = run_unit(db, reg, clock, emit=emit, present=common.domain_present)
        dt = time.time() - t0
        if dt > SLOW_STEP_S:
            print(f"⚠ SLOW STEP {dt:.1f}s — steps must never sleep (no-sleep law)", flush=True)
        if not worked:
            time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
