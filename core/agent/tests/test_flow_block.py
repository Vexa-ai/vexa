"""blocks.v1 `flow`: the server reads a flow block exactly as the terminal does (sanitize_fence)."""
import json
from pathlib import Path

from control_plane import home

GOLDEN = Path(__file__).resolve().parents[1] / "contracts" / "blocks.v1" / "golden" / "Fence.flow.json"


def test_a_flow_block_is_read_and_capped():
    f = home.sanitize_fence(json.loads(GOLDEN.read_text()))
    flow = next(b for b in f["blocks"] if b["type"] == "flow")["flow"]
    assert flow["trigger"] == {"kind": "schedule", "cron": "*/15 * * * *"} and flow["steps"] == ["agent_run"]


def test_a_flow_block_without_a_readable_trigger_or_steps_is_dropped():
    f = home.sanitize_fence({"v": 1, "blocks": [
        {"type": "flow", "flow": {"name": "x", "trigger": {"kind": "schedule", "cron": "x"}, "steps": ["agent_run"]}},
        {"type": "flow", "flow": {"name": "y", "trigger": {"kind": "event", "event": "e"}, "steps": ["<b>"]}}]})
    assert not (f or {}).get("blocks")
