"""Eval for the lifecycle claims audit (`Vexa-ai/vexa#1191`).

Drives the synthetic fixture file through the auditor and asserts, per row, the EXACT set of
findings — both directions. The fire cases replicate shapes observed in prod; the pass cases are
the false-positive guards, including both threshold boundaries (a 240s deaf-run candidate that
must not fire, a 926s admission timeout against a 900s budget that must not fire).

The expectations live here rather than inside the fixture so the fixture stays a plain rows file —
the same one `python -m meeting_api.lifecycle.claims_audit <rows.json>` consumes.

OFFLINE — a pure function over dicts. No docker, no DB, no network, no clock.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from meeting_api.lifecycle.claims_audit import (
    AuditParams,
    Finding,
    Severity,
    audit_meeting_row,
    audit_rows,
    load_rows,
    main,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "claims_audit_rows.json"

#: row id → the exact ordered finding codes that row must produce. `[]` means the row is clean.
EXPECTED: dict[str, list[str]] = {
    # ── pass: consistent rows ────────────────────────────────────────────────────────────────
    "clean-completed-stopped": [],
    "rejected-fast": [],
    "admission-timeout-at-budget": [],
    "clean-failed-join-failure": [],
    "short-completed-no-segments": [],
    "in-flight-active": [],
    # ── I1: reason × stage ───────────────────────────────────────────────────────────────────
    "left-alone-at-awaiting-admission": ["I1.reason_stage_mismatch"],
    "admission-timeout-at-joining": ["I1.reason_stage_mismatch"],
    "join-failure-at-active": ["I1.reason_stage_mismatch"],
    "failed-without-stage": ["I1.missing_failure_stage"],
    # ── I2: deaf run ─────────────────────────────────────────────────────────────────────────
    "deaf-run-completed-left-alone": ["I2.deaf_run"],
    # ── I3: duration envelopes ───────────────────────────────────────────────────────────────
    "left-alone-before-silence-window": ["I3.duration_envelope"],
    "rejected-too-long": ["I3.duration_envelope"],
    "admission-timeout-far-under-budget": ["I3.duration_envelope"],
    # ── I4: terminal attribution ─────────────────────────────────────────────────────────────
    "terminal-without-reason": ["I4.missing_completion_reason"],
    "silent-reconciliation-jump": [
        "I4.unnamed_transition_source",
        "I4.terminal_absent_from_trail",
    ],
}


def _fixture_rows() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _by_id() -> dict[str, dict]:
    return {row["id"]: row for row in _fixture_rows()}


def test_fixture_and_expectation_table_cover_the_same_rows():
    """A row added to the fixture without an expectation is an untested row."""
    assert sorted(_by_id()) == sorted(EXPECTED)


def test_fixture_carries_no_customer_identifiers():
    """The fixture is synthetic by contract — no emails, no meeting URLs, no real ids."""
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    for forbidden in ("@", "http://", "https://", "meet.google.com", "teams.microsoft", "zoom.us"):
        assert forbidden not in raw, f"fixture leaks {forbidden!r}"


@pytest.mark.parametrize("row_id", sorted(EXPECTED))
def test_row_produces_exactly_its_expected_findings(row_id):
    row = _by_id()[row_id]
    findings = audit_meeting_row(row)
    assert [finding.code for finding in findings] == EXPECTED[row_id]
    assert all(isinstance(finding, Finding) for finding in findings)
    assert all(finding.row_id == row_id for finding in findings)
    assert all(finding.invariant == finding.code.split(".", 1)[0] for finding in findings)


@pytest.mark.parametrize(
    "row_id", [rid for rid, codes in EXPECTED.items() if not codes],
)
def test_clean_rows_produce_no_false_positives(row_id):
    assert audit_meeting_row(_by_id()[row_id]) == []


def test_audit_meeting_row_is_pure():
    """No mutation of the caller's row — a sweep must be able to reuse the dict it passed in."""
    row = _by_id()["left-alone-at-awaiting-admission"]
    before = copy.deepcopy(row)
    audit_meeting_row(row)
    assert row == before


def test_nested_data_jsonb_is_read_like_the_top_level():
    """Prod writes the lifecycle fields into `meeting.data`; the auditor reads both placements."""
    row = _by_id()["left-alone-at-awaiting-admission"]
    assert "completion_reason" not in row and "completion_reason" in row["data"]
    assert [f.code for f in audit_meeting_row(row)] == ["I1.reason_stage_mismatch"]


def test_duration_falls_back_to_created_at_updated_at():
    """The deaf-run row carries no `duration_s`; its 660s comes from the timestamp pair."""
    row = _by_id()["deaf-run-completed-left-alone"]
    assert "duration_s" not in row
    (finding,) = audit_meeting_row(row)
    assert finding.code == "I2.deaf_run"
    assert "660s" in finding.message


def test_unmeasurable_duration_warns_rather_than_asserting():
    row = dict(_by_id()["rejected-fast"])
    row.pop("duration_s")
    codes = [finding.code for finding in audit_meeting_row(row)]
    assert codes == ["I3.duration_unmeasurable"]
    assert audit_meeting_row(row)[0].severity is Severity.WARN


# ── the envelope parameters are arguments, not constants ──────────────────────────────────────

def test_admission_budget_parameter_moves_the_timeout_envelope():
    """The 926s row is clean against a 900s budget and dirty against a 300s one."""
    row = _by_id()["admission-timeout-at-budget"]
    assert audit_meeting_row(row) == []
    codes = [f.code for f in audit_meeting_row(row, admission_budget_s=300.0)]
    assert codes == ["I3.duration_envelope"]


def test_silence_window_parameter_moves_the_left_alone_floor():
    """The 300s left_alone row is dirty against a 600s window and clean against a 120s one."""
    row = _by_id()["left-alone-before-silence-window"]
    assert [f.code for f in audit_meeting_row(row)] == ["I3.duration_envelope"]
    assert audit_meeting_row(row, silence_window_s=120.0) == []


def test_deaf_run_threshold_is_a_parameter():
    row = _by_id()["short-completed-no-segments"]
    assert audit_meeting_row(row) == []
    codes = [
        f.code
        for f in audit_meeting_row(row, params=AuditParams(deaf_run_min_duration_s=120.0))
    ]
    assert codes == ["I2.deaf_run"]


# ── the batch report ──────────────────────────────────────────────────────────────────────────

def test_report_over_the_whole_fixture():
    report = audit_rows(_fixture_rows())
    expected_findings = sum(len(codes) for codes in EXPECTED.values())
    expected_flagged = sum(1 for codes in EXPECTED.values() if codes)

    assert report.rows_seen == len(EXPECTED)
    assert report.rows_skipped == 1          # the single in-flight row
    assert report.rows_audited == len(EXPECTED) - 1
    assert len(report.findings) == expected_findings
    assert report.rows_flagged == expected_flagged
    assert report.ok is False
    assert report.by_invariant() == {"I1": 4, "I2": 1, "I3": 3, "I4": 3}


def test_report_over_clean_rows_only_is_ok():
    clean = [row for row in _fixture_rows() if not EXPECTED[row["id"]]]
    report = audit_rows(clean)
    assert report.ok is True
    assert report.findings == []
    assert "no findings" in report.render()


def test_report_params_reflect_applied_overrides():
    report = audit_rows(_fixture_rows(), admission_budget_s=300.0)
    assert report.params.admission_budget_s == 300.0
    assert report.to_dict()["params"]["admission_budget_s"] == 300.0


def test_report_serialises_and_renders():
    report = audit_rows(_fixture_rows())
    payload = report.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    rendered = report.render()
    assert "I1.reason_stage_mismatch" in rendered
    assert "left-alone-at-awaiting-admission" in rendered


def test_audit_rows_streams_a_generator():
    report = audit_rows(row for row in _fixture_rows())
    assert report.rows_seen == len(EXPECTED)


# ── the entrypoint ────────────────────────────────────────────────────────────────────────────

def test_load_rows_accepts_both_envelopes():
    rows = _fixture_rows()
    assert len(load_rows(rows)) == len(rows)
    assert len(load_rows({"rows": rows})) == len(rows)
    with pytest.raises(ValueError):
        load_rows("not rows")


def test_main_exits_1_on_findings(capsys):
    assert main([str(FIXTURE_PATH)]) == 1
    out = capsys.readouterr().out
    assert "lifecycle claims audit" in out
    assert "I2.deaf_run" in out


def test_main_exits_0_on_clean_rows(tmp_path, capsys):
    clean = [row for row in _fixture_rows() if not EXPECTED[row["id"]]]
    path = tmp_path / "clean.json"
    path.write_text(json.dumps(clean), encoding="utf-8")
    assert main([str(path)]) == 0
    assert "no findings" in capsys.readouterr().out


def test_main_json_output(capsys):
    main([str(FIXTURE_PATH), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["by_invariant"] == {"I1": 4, "I2": 1, "I3": 3, "I4": 3}


def test_main_reports_unreadable_input(tmp_path, capsys):
    assert main([str(tmp_path / "absent.json")]) == 2
    assert "cannot read rows" in capsys.readouterr().err


def test_module_is_runnable_as_python_m():
    """`python -m meeting_api.lifecycle.claims_audit <rows.json>` — the nightly sweep's shape."""
    src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(src))
    proc = subprocess.run(
        [sys.executable, "-m", "meeting_api.lifecycle.claims_audit", str(FIXTURE_PATH)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 1, proc.stderr
    assert "rows flagged" in proc.stdout
