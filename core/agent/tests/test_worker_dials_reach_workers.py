"""Every `VEXA_AGENT_*` dial the worker reads reaches the worker.

A dial reaches a worker one of two ways: agent-api stamps it into the dispatch
(`dispatch.build_unit_env`), or the runtime forwards it from its own environment
(`runtime_kernel.workload_env.WORKER_FORWARD_ENV`). The per-kind tool-call budgets
(`VEXA_AGENT_MAX_TOOL_CALLS_CHAT/_JOB/_ROOM/_FLOW`) and the job ceilings
(`VEXA_AGENT_JOB_MAX_*`) had neither, so a deployment's setting never left the runtime.
"""
from __future__ import annotations

import re
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
CORE = AGENT.parent
#: Built from a kind at run time (`f"VEXA_AGENT_MAX_TOOL_CALLS_{kind}"`), so named here.
PER_KIND = {f"VEXA_AGENT_MAX_TOOL_CALLS_{k}" for k in ("CHAT", "JOB", "ROOM", "FLOW")}


def _forwarded() -> set:
    src = (CORE / "runtime" / "src" / "runtime_kernel" / "workload_env.py").read_text()
    block = src[src.index("WORKER_FORWARD_ENV = ("):]
    return set(re.findall(r'"([A-Z0-9_]+)"', block[: block.index("\n)\n")]))


def _read_by_worker() -> set:
    names: set = set(PER_KIND)
    for p in [*(AGENT / "llm").glob("*.py"), *(AGENT / "worker").glob("*.py")]:
        names |= set(re.findall(r'"(VEXA_AGENT_[A-Z0-9_]*[A-Z0-9])"', p.read_text()))
    return names


def test_every_dial_the_worker_reads_is_stamped_or_forwarded():
    stamped = (AGENT / "control_plane" / "dispatch.py").read_text()
    missing = sorted(k for k in _read_by_worker() if k not in _forwarded() and k not in stamped)
    assert missing == [], f"read by the worker, but neither stamped by dispatch nor forwarded: {missing}"


def test_the_runtime_declares_every_dial_it_forwards():
    import json
    declared = {k["key"] for k in json.loads(
        (CORE / "runtime" / "src" / "runtime_kernel" / "config.v1.json").read_text())["keys"]}
    assert PER_KIND | {"VEXA_AGENT_JOB_MAX_TOOL_CALLS", "VEXA_AGENT_JOB_MAX_TURN_SEC"} <= declared
