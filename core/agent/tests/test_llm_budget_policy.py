"""The spend-cap keys are one set in three places: agent-api's table (`llm_budget.DEFAULTS`), the
flows table that lists every key `_global/POLICIES.md` may carry, and the seeded file itself."""
from __future__ import annotations

import ast
from pathlib import Path

from control_plane import front_page
from control_plane import llm_budget as lb

REPO = Path(__file__).resolve().parents[3]
FLOWS_POLICIES = REPO / "core" / "flows" / "src" / "flows_steps" / "policies.py"
SEED = REPO / "behavior" / "global" / "POLICIES.md"


def _flows_defaults() -> dict:
    tree = ast.parse(FLOWS_POLICIES.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "DEFAULTS")
    return {k.value: v.value for k, v in zip(node.value.keys, node.value.values)
            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}


def _same(a, b) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a or "") == str(b or "")


def test_flows_lists_every_spend_key_at_agent_apis_default():
    flows = _flows_defaults()
    for key, default in lb.DEFAULTS.items():
        assert key in flows, key
        assert _same(flows[key], default), (key, flows[key], default)


def test_the_seed_declares_every_spend_key_at_its_default():
    declared = front_page.front_matter(SEED.read_text())
    for key, default in lb.DEFAULTS.items():
        assert key in declared, key
        assert _same(declared[key], default), (key, declared[key], default)
    assert lb.Caps.from_policy(declared) == lb.Caps.from_policy({})
