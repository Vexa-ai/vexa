"""L3 · Worker over a synthetic transcript (offline) — the worker→cookbook vertical.

The fixture boundary is the transcript: a FIXED slice of the committed `gamestop-allin.jsonl` is replayed
through `worker.meeting_card_turn` with a DETERMINISTIC CompletionPort fake (no provider, no redis, no
docker), and the emitted events are folded EXACTLY as the cookbook's `harvest()` folds the live redis
stream.

This proves the **cross-level identity** the ladder is built on: the worker's offline output carries the
same `{type, note/card}` event shape the live stream does, so it folds into the cookbook's `Harvest`
carrier identically — replay == live, at the data level. (The deep transcript→entities assertions live in
`test_meeting_postprocess_offline.py`; here we assert the fold into the cookbook's carrier.)
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "clients" / "slim" / "vexa_slim").is_dir():
            return parent
    raise FileNotFoundError("repo root with clients/slim not found")


sys.path.insert(0, str(_repo_root() / "clients" / "slim"))   # make vexa_slim importable here

from tests.test_meeting_postprocess_offline import (         # noqa: E402  reuse the offline harness
    _RECORDED_REPLY,
    _fake_completion,
    _load_fixture_segments,
)
from vexa_slim.harvest import Harvest                         # noqa: E402  the cookbook's carrier
from worker import worker                                     # noqa: E402


def _fold_like_cookbook(events: list) -> Harvest:
    """The exact fold `vexa_slim.harvest.harvest` applies to live redis events — group by `type`."""
    h = Harvest()
    for evt in events:
        h.by_type.setdefault(evt.get("type", "?"), []).append(evt)
    return h


def test_worker_offline_output_folds_into_cookbook_harvest(tmp_path):
    segments = _load_fixture_segments(4)
    completion, _captured = _fake_completion(_RECORDED_REPLY)

    with mock.patch.object(worker, "completion_factory", lambda: completion):
        events = list(worker.meeting_card_turn(
            tmp_path, segments, model="openrouter/free",
            card_kinds=["person", "company", "product"],
        ))

    harvest = _fold_like_cookbook(events)

    # the worker's events fold into the cookbook's carrier with notes + cards populated
    assert harvest.of("note") and harvest.of("card")
    assert [n["note"]["id"] for n in harvest.of("note")] == ["seg-0", "seg-1", "seg-2", "seg-3"]
    assert {c["card"]["title"] for c in harvest.of("card")} == {"Ryan Cohen", "GameStop", "AppLovin"}
    # counts() is the cookbook's user-facing summary — same shape a live watch would yield
    assert harvest.counts()["note"] == 4
