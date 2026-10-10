"""meeting-bundle.v1 conformance: the exporter and the importer are held to the SAME goldens the
Node validator re-derives (`deploy/contracts/meeting-bundle.v1/validate.mjs`).

* every golden bundle is byte-for-byte what the shipped writer produces from its fixed inputs;
* the shipped exporter (`export_meeting`, over the stores) produces the transcript-only golden
  exactly, so the export route and the golden cannot drift;
* the shipped importer accepts every golden bundle and refuses every refused golden with the code
  `golden/refused/refused.json` names — the code the Node validator gives the same file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

import bundle_goldens as G
from meeting_api.bundle import BundleRefused, deployment_id, export_meeting, read_bundle
from meeting_api.bundle.codec import conform
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.recordings import finalize_master
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

REGEN = os.environ.get("MEETING_BUNDLE_REGEN") == "1"


def _check_or_write(path, data: bytes):
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert path.is_file(), f"missing golden {path} — regenerate with MEETING_BUNDLE_REGEN=1"
    assert path.read_bytes() == data, f"{path.name} differs from what the shipped code writes"


@pytest.mark.parametrize("name", G.GOOD)
def test_golden_bundle_is_what_the_writer_produces(name):
    _check_or_write(G.GOLDEN / "bundles" / f"{name}.zip", G.golden_bundle(name))


@pytest.mark.parametrize("name", sorted(G.REFUSED))
def test_refused_golden_is_what_the_fixture_builds(name):
    _check_or_write(G.GOLDEN / "refused" / f"{name}.zip", G.refused_bundle(name))


def test_refused_table_names_every_refused_golden():
    rows = [{"bundle": f"{n}.zip", "code": c, "why": w} for n, (c, w) in sorted(G.REFUSED.items())]
    _check_or_write(G.GOLDEN / "refused" / "refused.json",
                    (json.dumps(rows, indent=2) + "\n").encode())
    for row in rows:
        assert conform("RefusedVector", row) is None


def test_golden_deployment_id_is_the_derivation():
    with (G.GOLDEN / "bundles" / "transcript-only.zip").open("rb") as fh:
        manifest = read_bundle(fh.read()).manifest
    assert manifest["source"]["deployment_id"] == deployment_id(G.GOLDEN_SECRET)


@pytest.mark.parametrize("name", G.GOOD)
def test_importer_accepts_every_golden_bundle(name):
    parsed = read_bundle((G.GOLDEN / "bundles" / f"{name}.zip").read_bytes())
    assert [s["text"] for s in parsed.transcript["segments"]] == [s["text"] for s in G.SEGMENTS]
    assert len(parsed.media) == (1 if name == "with-audio" else 0)


@pytest.mark.parametrize("name", sorted(G.REFUSED))
def test_importer_refuses_every_refused_golden_with_its_code(name):
    with pytest.raises(BundleRefused) as refused:
        read_bundle((G.GOLDEN / "refused" / f"{name}.zip").read_bytes())
    assert refused.value.code == G.REFUSED[name][0], refused.value.detail


async def test_the_shipped_exporter_writes_the_golden_bytes():
    store = InMemoryTranscriptStore()
    m = G.meeting([])
    mid = store.seed_meeting(
        user_id=1, platform=m["platform"], native_meeting_id=m["native_meeting_id"], status="completed",
        meeting_id=G.SOURCE_MEETING_ID, start_time=m["start_time"], end_time=m["end_time"],
        data={"title": m["title"], "metadata": G.ANNOTATIONS["metadata"]},
        segments=[{**s, "segment_id": f"s-{i}"} for i, s in enumerate(G.SEGMENTS)],
    )
    archive, filename = await export_meeting(
        store, InMemoryRecordingRepo(), InMemoryStorage(), user_id=1, meeting_id=mid,
        secret=G.GOLDEN_SECRET, finalize=finalize_master,
        now=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc), bundle_id=G.BUNDLE_ID)
    assert filename == "release-sync.meeting-bundle.zip"
    assert archive == (G.GOLDEN / "bundles" / "transcript-only.zip").read_bytes()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_node_validator_agrees():
    """The second-language half: the contract's own validator, run on the same goldens."""
    root = G.CONTRACT_DIR.parents[2]
    if not (root / "node_modules" / "ajv").exists():
        pytest.skip("repo node_modules not installed")
    out = subprocess.run(["node", str(G.CONTRACT_DIR / "validate.mjs"), "--check"],
                         capture_output=True, text=True, cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
