"""The DNA replay, as a test — so decision 26's proof obligation runs on every push, not on a memory.

`eval/workspace_links_replay.py` is the runnable artefact (it prints the numbers a human reads). This
runs the same function and asserts the six checks, skipping when the fixture library is not on this
machine — the fixtures are the founder's meeting transcripts and they are deliberately not in the
repo.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

FIXTURES = pathlib.Path(os.environ.get("VEXA_DNA_FIXTURES", "~/dna-fixtures")).expanduser()
REPLAY = pathlib.Path(__file__).resolve().parents[1] / "eval" / "workspace_links_replay.py"


def _load():
    spec = importlib.util.spec_from_file_location("workspace_links_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(len(sorted(FIXTURES.glob("*.truth.yaml"))) < 2 if FIXTURES.is_dir() else True,
                    reason="the DNA fixture library is not on this machine")
def test_links_between_a_desk_and_a_group_survive_the_group_being_renamed(capsys, monkeypatch):
    mod = _load()
    monkeypatch.setattr("sys.argv", ["replay", "--fixtures", str(FIXTURES), "--json"])
    assert mod.main() == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True, report["checks"]
    assert all(report["checks"].values()), report["checks"]
    # The numbers this replay exists to report, asserted rather than merely printed.
    assert report["links"]["written_in_id_form"] > 0
    assert report["links"]["as_the_desk_owner"]["readable"] == report["links"]["distinct_refs"]
    assert report["links"]["as_a_non_member"]["not-yours"] == report["links"]["distinct_refs"]
    assert report["links"]["as_the_desk_owner"]["gone"] == 0


def test_the_replay_reads_a_truth_sidecar_without_a_yaml_dependency(tmp_path):
    """The parser is a regex on purpose — a proof that needs a new dependency stops being run."""
    mod = _load()
    (tmp_path / "x.truth.yaml").write_text(
        'date: 2026-03-02\n'
        'present: ["Cottalango Leon (Sony Pictures Imageworks)", "Sam Richards"]\n'
        'decided:\n'
        '  - "TSC membership and commit privileges are separate grants"\n'
        'committed:\n'
        '  - "Circulate the charter"\n')
    t = mod.read_truth(tmp_path / "x.truth.yaml")
    assert t["date"] == "2026-03-02"
    assert t["people"] == ["Cottalango Leon", "Sam Richards"]
    assert t["orgs"] == ["Sony Pictures Imageworks"]
    assert t["decided"] == ["TSC membership and commit privileges are separate grants"]
    assert t["committed"] == ["Circulate the charter"]
