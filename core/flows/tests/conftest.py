import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


#: THE ADMIN IDENTITY, declared the way a deployment declares it — AT IMPORT, beside the doors.
#:
#: `VEXA_FLOWS_ADMIN_KEY` lost its `changeme` default (R-B11): it opens `ensure_platform_user` and
#: `user_api_key`, so an unset value REFUSES rather than trying a placeholder. That makes every
#: offline test that touches a step raise on the refusal instead of on the thing it is testing, so
#: the suite declares a key, exactly as the lane's start script does.
#:
#: IT WAS AN AUTOUSE FIXTURE, and a fixture runs far too late (F-D20 b): flows-api now validates
#: its whole `config.v1` declaration at import, and six test modules import it at MODULE SCOPE —
#: i.e. during collection, before any fixture has run. The refusal was correct and the test process
#: was the thing misconfigured. Same reasoning, verbatim, as the doors below.
#:
#: `setdefault` semantics on purpose: a run that already exports a real key — the live contract
#: smoke against a running admin-api — keeps it. The tests that are ABOUT the refusal unset it
#: themselves with `monkeypatch.delenv`, which is per-test and unaffected.
os.environ.setdefault("VEXA_FLOWS_ADMIN_KEY", "test-admin-key-not-a-placeholder")


#: The doors, declared for the test process the way a deployment declares them. NOT autouse-set to
#: localhost: the whole point of the change that made these required is that a host-port default
#: silently addresses whatever else is listening — on 2026-09-03 a bare `pytest` run of
#: `test_admin_user_lookup_shapes` reached `vexa-v012`'s admin-api through the old
#: `http://localhost:18057` default and read its 403 as this stack's answer. A test that wants a
#: LIVE service exports the real URL (the contract smoke does, and skips when it is absent); every
#: offline test gets an address that is unmistakably not a service, so a step that tries to reach
#: one fails loudly instead of hitting a neighbour.
OFFLINE_DOORS = {
    # `127.0.0.1:1` on purpose. Port 1 is never a service, so a step that reaches a door it should
    # not have gets an immediate CONNECTION REFUSED — the same shape the old localhost defaults
    # produced when nothing was listening, and never the shape they produced when something was.
    # A `.invalid` hostname would be tidier to read and worse to run: it costs a DNS lookup that
    # fails as a different error class, so an offline test would diverge from a real refusal.
    "VEXA_FLOWS_GATEWAY_URL": "http://127.0.0.1:1",
    "VEXA_FLOWS_ADMIN_API_URL": "http://127.0.0.1:1",
    "VEXA_UI_URL": "http://ui.test",
    # The agent door is a CAPABILITY (PRD decision 40.7) and its absence is a supported product —
    # but almost every test in this suite is about the FULL profile, so the suite declares it and
    # `test_no_agents.py` unsets it deliberately for the one contract that is about the other one.
    "VEXA_FLOWS_AGENT_API_URL": "http://127.0.0.1:1",
}


# APPLIED AT IMPORT, not in a fixture. `flows_steps/meeting.py` and friends resolve their door at
# module import, so a fixture would run long after collection has already failed — and making the
# doors lazy enough to be set per-test would put the import-time refusal back where it cannot be
# seen. A test process declares its doors before anything imports a step module, exactly as a
# deployment does before it boots.
for _key, _value in OFFLINE_DOORS.items():
    os.environ.setdefault(_key, _value)
