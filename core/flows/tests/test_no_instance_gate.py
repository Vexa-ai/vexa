"""No instance gate: flows act on the world whatever `_global` holds.

Founder ruling 2026-10-08: "let's remove global setup at all so that there is no need to setup
global at all - let it be empty with no data - it's fine." Until then the worker parked every
reaction and the two operator verbs (`POST /flows`, `POST /flows/{name}/{v}/{action}`) answered
409 while admin-api said the company layer was unwritten. Both are gone, and nothing reads that
state any more.

The API half is asserted against the source rather than imported, for the reason the old gate
suite gave: importing `flows_api` needs its own credential + DB-URL env at import time, which is
process-wide poison for every other test file (`gate:test-isolation`).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixtures import INVITE_REFS, drain, rig  # noqa: E402
from flows import admit, tick  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"


def test_the_engine_runs_an_admitted_fact_with_no_company_layer_anywhere():
    """No gate to consult: an admitted fact is claimed and run on the first tick."""
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="ev-1", event_type="invite.received", subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    assert tick(db, reg, clock) is True
    drain(db, reg, clock)
    assert world.meetings_created == ["m-1"]
    assert db.execute("SELECT status FROM reaction")[0][0] == "done"


def test_tick_has_no_gate_seam_left():
    assert "gate" not in inspect.signature(tick).parameters


def test_the_worker_injects_no_gate():
    worker = (SRC / "flows_worker" / "__main__.py").read_text()
    assert "gate=" not in worker and "instance_gate" not in worker


def test_no_operator_verb_or_intake_consults_a_company_layer():
    api = (SRC / "flows_integrations" / "flows_api.py").read_text()
    for gone in ("instance_gate", "_refuse_if_gated", "_with_gate", "company_layer_ready"):
        assert gone not in api, gone
    assert not (SRC / "flows_integrations" / "instance_gate.py").exists()
