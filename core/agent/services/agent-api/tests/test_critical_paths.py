"""Critical-path determinism tests (see docs/CONTROL-PLANE.md §4).

Each critical path is proven the same way: the simplest perfect fixture in → frozen output, asserted
BYTE-IDENTICAL across two runs (same in ⇒ same out — the ``gate:replay`` determinism discipline). LLM
nondeterminism is removed by a stubbed ``card_turn``. This module makes the CP catalog legible and guards
the plumbing the SoC refactor touches; the per-path behavioural depth still lives in ``test_ingest.py``
(CP1), ``test_worker.py`` (CP3/CP4) and ``test_transcription_watcher.py`` (CP3 arm).
"""
from __future__ import annotations

import json

from agent_api.worker import serve_meeting


class MeetingStream:
    """Deterministic in-memory transcript stream (same shape as test_worker's helper)."""

    def __init__(self, inbox):
        self.out = []
        self._inbox = list(inbox)

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        tname = next(iter(streams))
        if not self._inbox:
            return []
        batch, self._inbox = self._inbox, []
        return [(tname, list(batch))]


def _seg(speaker, text, completed=True):
    return {"speaker": speaker, "text": text, "completed": completed}


def _transcript(eid, *segs):
    return (eid, {"payload": json.dumps({"type": "transcription", "segments": list(segs)})})


def _stub_card_turn(segments):
    # deterministic: output depends only on the input segments, never a model call
    yield {"type": "card", "card": {"kind": "person", "title": "Raj", "body": "internal lead"}}
    yield {"type": "card", "card": {"kind": "action", "title": "Send SSO docs", "body": "by EOW"}}


def _run_cp4_once():
    s = MeetingStream(inbox=[_transcript(
        "1-0", _seg("Jane", "let's discuss pricing"), _seg("Raj", "we need SSO first"))])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="unit:u:out",
                  card_turn=_stub_card_turn, idle_ms=10)
    return s.out


def test_cp4_copilot_turn_byte_identical_across_runs():
    """CP4: fixture transcript → copilot turn → emitted events. Same in ⇒ same out, byte-identical."""
    run1, run2 = _run_cp4_once(), _run_cp4_once()
    assert json.dumps(run1, sort_keys=True) == json.dumps(run2, sort_keys=True)
    # not vacuously equal: the expected cards were actually emitted
    titles = {json.loads(f["event"]).get("card", {}).get("title")
              for _t, f in run1 if json.loads(f["event"]).get("type") == "card"}
    assert {"Raj", "Send SSO docs"} <= titles
