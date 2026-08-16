"""Replay the calibration corpus — the test that actually caught the bug.

The corpus is **not in this repository**: it is 22 recordings from the founder's own
archive, and two of them are exactly the private material this gate exists to keep off
an email thread. Point an operator-held copy at the test to run it:

    VEXA_PRESEND_CORPUS=/path/to/corpus uv run pytest tests/test_corpus.py -q

The directory holds the transcript payloads as `*.json` plus one
`presend-expectations.json` naming, per record, whether it is a meeting. Without the env
var the test skips, so the repo's gate stays green without the private data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from presend_gate import Outcome, evaluate, from_transcript_payload

CORPUS = os.environ.get("VEXA_PRESEND_CORPUS")
EXPECTATIONS = "presend-expectations.json"

pytestmark = pytest.mark.skipif(
    not CORPUS or not Path(CORPUS).is_dir(),
    reason="VEXA_PRESEND_CORPUS is not set — the calibration corpus is private and not vendored",
)


@pytest.fixture(scope="module")
def verdicts():
    root = Path(CORPUS)
    spec = json.loads((root / EXPECTATIONS).read_text(encoding="utf-8"))
    out = {}
    for path in sorted(root.glob("*.json")):
        if path.name == EXPECTATIONS:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "segments" not in payload:
            continue
        record = from_transcript_payload(
            payload, creator=spec.get("creator"), bot_names=spec.get("bot_names", [])
        )
        out[path.stem] = evaluate(record)
    assert out, "corpus directory held no transcript payloads"
    return spec, out


def test_no_non_meeting_is_ever_sent(verdicts):
    """The finding, as an assertion. These two rendered clean, sendable artifacts."""
    spec, results = verdicts
    for record_id in spec["not_meetings"]:
        assert results[record_id].outcome is not Outcome.SEND, (
            f"{record_id} is not a meeting and would have been emailed to its participants"
        )


def test_real_meetings_are_sent(verdicts):
    spec, results = verdicts
    holds = spec.get("known_holds", {})
    for record_id in spec["real_meetings"]:
        if record_id in holds:
            continue
        assert results[record_id].outcome is Outcome.SEND, (
            f"{record_id} is a real meeting held back by {results[record_id].reasons}"
        )


def test_known_holds_stay_held(verdicts):
    """The honest false positives, pinned so a threshold change surfaces them.

    Each entry is a genuine meeting the gate cannot confirm — holding it costs the
    creator one click; sending it would have cost the guarantee.
    """
    spec, results = verdicts
    for record_id, why in spec.get("known_holds", {}).items():
        assert results[record_id].outcome is Outcome.HOLD_FOR_CREATOR, why


def test_every_corpus_record_is_accounted_for(verdicts):
    spec, results = verdicts
    labelled = set(spec["not_meetings"]) | set(spec["real_meetings"])
    assert set(results) == labelled, "corpus and expectations have drifted apart"
