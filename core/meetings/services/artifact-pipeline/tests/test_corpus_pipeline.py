"""Replay the whole pipeline over the real archive — the run that has to be right.

The corpus is **not in this repository**: 22 recordings from the founder's own archive, two
of which are exactly the private material the pre-send gate exists to keep off an email
thread. Point an operator-held copy at the test:

    VEXA_ARTIFACT_CORPUS=/path/to/corpus uv run pytest tests/test_corpus_pipeline.py -q -s

The directory holds one ``<id>.json`` per record plus ``presend-expectations.json`` naming,
per record, whether it is a meeting. Without the env var the whole module skips, so the
repository's gate stays green without the private data.

Everything in the loop is real except two ends: the meeting API is served from the corpus
files through the shipped HTTP client (:class:`CorpusTransport`), and the delivery sink
records instead of mailing. The **gate is the real module** — a fake gate here would defeat
the point of the run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import RecordingDelivery
from vexa_artifact_pipeline import (
    ArtifactPipeline,
    CompletedMeeting,
    CorpusTransport,
    DeliveryResult,
    HttpMeetingGateway,
    MemoryRunLog,
    RosterDirectory,
)

CORPUS = os.environ.get("VEXA_ARTIFACT_CORPUS")
EXPECTATIONS = "presend-expectations.json"

pytestmark = pytest.mark.skipif(
    not CORPUS or not Path(CORPUS).is_dir(),
    reason="VEXA_ARTIFACT_CORPUS is not set — the archive is private and not vendored",
)


def _run(root: Path, spec: dict, *, delivery, log, ids):
    """One pass of the pipeline over the corpus, keyed by FILENAME, not by record id.

    Triggering on the filename is what a directory-driven caller does, and it is how the
    requested id and the record's own id come apart on six of the twenty-two records.
    """
    gateway = HttpMeetingGateway("http://meetings.corpus", transport=CorpusTransport(root))
    pipeline = ArtifactPipeline(
        gateway=gateway,
        directory=RosterDirectory(),
        delivery=delivery,
        run_log=log,
    )
    try:
        return {
            stem: pipeline.run(
                CompletedMeeting(
                    meeting_id=stem,
                    creator=spec.get("creator"),
                    bot_names=tuple(spec.get("bot_names", ())),
                )
            )
            for stem in ids
        }
    finally:
        gateway.close()


@pytest.fixture(scope="module")
def corpus():
    root = Path(CORPUS)
    spec = json.loads((root / EXPECTATIONS).read_text("utf-8"))
    ids = sorted(p.stem for p in root.glob("*.json") if p.name != EXPECTATIONS)
    assert ids, "corpus directory held no record payloads"

    log = MemoryRunLog()
    delivery = RecordingDelivery(requires_address=False)
    first = _run(root, spec, delivery=delivery, log=log, ids=ids)

    print()
    for stem in ids:
        r = first[stem]
        drift = "" if r.id_matches_request else f" (file {stem})"
        print(
            f"  {r.meeting_id:>6}{drift:<12} {r.verdict:<17} "
            f"artifacts={len(r.artifacts):<2} sent={len(r.sent):<2} {','.join(r.reasons)}"
        )
    return {"root": root, "spec": spec, "ids": ids, "results": first, "log": log, "delivery": delivery}


# ── the finding, as an assertion ──────────────────────────────────────────────────────


def test_no_non_meeting_reaches_a_participant(corpus):
    """Both of these rendered clean, sendable artifacts before the gate existed."""
    for record_id in corpus["spec"]["not_meetings"]:
        result = corpus["results"][record_id]
        assert result.verdict != "send", f"{record_id} would have been broadcast"
        strangers = [o for o in result.outcomes if o.status == DeliveryResult.SENT and not o.recipient.is_creator]
        assert strangers == [], f"{record_id} reached {[o.recipient.display_name for o in strangers]}"


def test_a_suppressed_record_produces_no_artifact_at_all(corpus):
    """Gate before render: for a suppressed record nothing is ever built, so there is no
    artifact on disk, in a log or in a queue for a later code path to send by accident."""
    for record_id in corpus["spec"]["not_meetings"]:
        result = corpus["results"][record_id]
        if result.verdict == "suppress":
            assert result.artifacts == ()
            assert result.recipients == ()


def test_real_meetings_are_sent(corpus):
    holds = corpus["spec"].get("known_holds", {})
    for record_id in corpus["spec"]["real_meetings"]:
        if record_id in holds:
            continue
        result = corpus["results"][record_id]
        assert result.verdict == "send", f"{record_id} held back by {result.reasons}"


def test_known_holds_stay_held(corpus):
    """The honest false positives, pinned so a threshold change surfaces them."""
    for record_id, why in corpus["spec"].get("known_holds", {}).items():
        assert corpus["results"][record_id].verdict == "hold_for_creator", why


def test_every_corpus_record_is_accounted_for(corpus):
    labelled = set(corpus["spec"]["not_meetings"]) | set(corpus["spec"]["real_meetings"])
    assert set(corpus["ids"]) == labelled, "corpus and expectations have drifted apart"


# ── fan-out ───────────────────────────────────────────────────────────────────────────


def test_a_multi_party_meeting_fans_out_one_artifact_per_participant(corpus):
    multi = [
        (stem, r)
        for stem, r in corpus["results"].items()
        if r.verdict == "send" and len(r.participants) >= 3
    ]
    assert multi, "the corpus holds no multi-party meeting that cleared the gate"

    for stem, result in multi:
        names = [a.recipient.display_name for a in result.artifacts]
        assert len(names) == len(set(names)) == len(result.recipients), stem
        for artifact in result.artifacts:
            body = artifact.to_markdown()
            assert body.startswith("**To:**") or body.startswith("**Кому:**"), stem
            assert artifact.recipient.display_name in body.splitlines()[0], stem


def test_each_artifact_is_addressed_to_exactly_one_person(corpus):
    for stem, result in corpus["results"].items():
        for artifact in result.artifacts:
            first_line = artifact.to_markdown().splitlines()[0]
            assert first_line.count(":**") == 1, (stem, first_line)


# ── the record's own id ───────────────────────────────────────────────────────────────


def test_the_record_id_used_is_the_records_own_not_the_filename(corpus):
    root = corpus["root"]
    drifted = []
    for stem, result in corpus["results"].items():
        stated = str(json.loads((root / f"{stem}.json").read_text("utf-8"))["id"])
        assert result.meeting_id == stated, f"{stem}: pipeline used {result.meeting_id}, record says {stated}"
        for artifact in result.artifacts:
            assert artifact.meeting_id == stated
            assert f"{stated} · " in artifact.to_markdown().splitlines()[2]
        if stated != stem:
            drifted.append((stem, stated))
    assert drifted, "expected the corpus to key some records under a different id than they state"


# ── idempotency ───────────────────────────────────────────────────────────────────────


def test_a_second_pass_over_the_whole_corpus_delivers_nothing_twice(corpus):
    second_delivery = RecordingDelivery(requires_address=False)
    second = _run(
        corpus["root"], corpus["spec"], delivery=second_delivery, log=corpus["log"], ids=corpus["ids"]
    )
    assert corpus["delivery"].sent, "the first pass delivered nothing — nothing to re-check"
    assert second_delivery.sent == []
    for stem, result in second.items():
        first = corpus["results"][stem]
        expected = {DeliveryResult.DUPLICATE} if first.sent else {o.status for o in first.outcomes}
        assert {o.status for o in result.outcomes} <= expected | {DeliveryResult.SUPPRESSED}, stem
