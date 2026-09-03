"""The rough-edges replay, as a test — so decision 33's proof obligation runs on every push.

`eval/friction_loop_replay.py` is the runnable artefact (it prints the brief a human reads). This
runs the same function and asserts the same claims. It needs no fixtures and no stack, so unlike the
DNA replay beside it there is nothing here to skip: if this ever cannot run, the loop is broken.
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


def test_three_synthetic_edges_produce_a_dump_a_fixing_agent_can_work_off(monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "u_jane")
    res = _load().run(out=io.StringIO())
    assert res["filed"] == 4 and res["rows"] == 3 and res["findings"] == 3
    assert res["deduped"] == 2                       # one edge reported twice is ONE row
    assert res["recurring"] == ["no-page"]           # and a fix that did not hold says so
    assert res["fix_that_did_not_hold"] == "PR #1410 · a3742c4"
    # The dump is the deliverable. These are the parts that make it usable WITHOUT this session.
    for part in ("**Symptom**", "**Exact context**", "**Likely cause**", "**Logs**", "**Repro**",
                 "docker logs --since", "friction_fixed(", "not a diagnosis"):
        assert part in res["dump"], part
