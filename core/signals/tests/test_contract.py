"""signals.v1: the goldens conform, and what the code writes and sends conforms too."""
import json
from pathlib import Path

import jsonschema
import pytest

from meeting_signals import brief as briefs
from meeting_signals.core import Caps, Listener, State
from meeting_signals.model import Line, Scored
from conftest import run

HERE = Path(__file__).resolve().parents[1] / "contracts" / "signals.v1"
SCHEMA = json.loads((HERE / "signals.schema.json").read_text())


def check(shape, value):
    jsonschema.validate(value, {**SCHEMA, "$ref": f"#/$defs/{shape}"})


@pytest.mark.parametrize("path", sorted((HERE / "golden").glob("*.json")), ids=lambda p: p.name)
def test_goldens(path):
    check(path.name.split(".")[0], json.loads(path.read_text()))


def test_what_signals_writes_and_sends_conforms():
    async def score(asks, line, ctx):
        return Scored({k: 0.7 for k in asks}, 10, "typesafe/jev-1.13")
    lst = Listener({"kind": "meeting", "id": "42"}, scorer=score, caps=Caps(30, 1.0), now=lambda: 1760000000.0)
    st = State()
    b = briefs.normalize({"labels": [{"name": "decision", "wake": True}], "version": 4})
    ann, _ = run(lst.take(st, [Line("seg-1", "A", "we decide", 1.0, 2.0)], b))
    check("Annotation", ann[0])
    check("WakeData", lst.wake_data(st, b, "threshold", {"meeting_row": "42"}))
