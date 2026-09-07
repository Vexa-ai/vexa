"""The rough-edges replay, as a test — so decision 33's proof obligation runs on every push.

`eval/friction_loop_replay.py` is the runnable artefact (it prints the brief a human reads). This
runs the same function and asserts the same claims. Reduced for #1510: the carrier moved to flows
and agent-api's own store (dedup, status machine, dump) is gone with it — see the replay's own
module docstring for what is still provable offline and what now needs a rig-side rehearsal
against a real flows-api instead.
"""
from __future__ import annotations

import importlib.util
import io
import pathlib

REPLAY = pathlib.Path(__file__).resolve().parents[1] / "eval" / "friction_loop_replay.py"


def _load():
    spec = importlib.util.spec_from_file_location("friction_loop_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_three_synthetic_edges_all_reach_the_flows_carrier(monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "u_jane")
    res = _load().run(out=io.StringIO())
    assert res["filed"] == 3
    assert res["all_recorded"]
    assert res["publish_count"] == 3          # three producers, three publishes — no dedup any more
    assert res["sessions"] == ["meet-104"]
    assert res["refused_with_no_session_status"] == 400
