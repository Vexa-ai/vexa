"""A flood of identical friction reports folds into ONE row with a count.

A reporter stuck in a loop (a scheduled run refused on every tick) used to file one
`friction.reported` reaction per pass. `POST /friction` now keys each report on
(uid, tool, normalised reason, UTC hour): the first is admitted as before, a repeat inside the same
hour bumps `occurrences`/`last_seen` on it and admits nothing, and the reporter is told to stop.

Offline: the real app through `TestClient`, a `SqliteDB`, and a `FakeClock` swapped in for the
route's clock so the hour bucket is deterministic.
"""
from __future__ import annotations

import os

import pytest
from sqlite_double import SqliteDB

from flows.clock import FakeClock
from flows_integrations import friction_dedup

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-friction",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://friction:unreachable@127.0.0.1:1/flows"}

#: 2026-10-10T10:00:00Z — the top of an hour, so `+3599` stays inside it and `+3600` leaves it.
HOUR_START = 1_791_626_400.0

REFUSAL = ('gmail_search returned {"status": "refused", "reason": "human_session_required"} '
           "at 2026-10-10T10:00:03Z for message 19a2f3c4d5e6f708")


@pytest.fixture()
def api():
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    flows_api.db = SqliteDB()
    saved_clock = flows_api.clock
    flows_api.clock = FakeClock(HOUR_START)
    try:
        yield flows_api, TestClient(flows_api.app)
    finally:
        flows_api.clock = saved_clock


def _post(flows_api, client, uid="126", **kw):
    return client.post("/friction", json=kw,
                       headers={"X-Flows-Operator-Key": flows_api.API_KEY, "X-User-Id": uid})


def _reactions(flows_api) -> int:
    return len(flows_api.db.execute(
        "SELECT reaction_id FROM reaction WHERE event_type = 'friction.reported'"))


def _reports(flows_api, client, uid="126") -> list:
    return client.get("/friction", headers={"X-Flows-Operator-Key": flows_api.API_KEY,
                                            "X-User-Id": uid}).json()["reports"]


def test_ten_identical_reports_are_one_reaction_with_occurrences_ten(api):
    flows_api, client = api
    answers = []
    for _ in range(10):
        answers.append(_post(flows_api, client, tool="gmail_search", what_happened=REFUSAL,
                             what_i_tried="check my email").json())
        flows_api.clock.advance(60)
    assert _reactions(flows_api) == 1, "a repeat was admitted as a reaction of its own"
    assert answers[0]["recorded"] is True
    first = answers[0]["id"]
    reports = _reports(flows_api, client)
    assert len(reports) == 1
    assert reports[0]["id"] == first
    assert reports[0]["occurrences"] == 10
    assert reports[0]["last_seen"] > reports[0]["at"]


def test_a_duplicate_answer_tells_the_reporter_to_stop(api):
    flows_api, client = api
    first = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    second = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    third = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    assert first["recorded"] is True and "duplicate_of" not in first
    assert second["recorded"] is False
    assert second["duplicate_of"] == first["id"]
    assert second["occurrences"] == 2
    assert second["instruction"] == "already reported 2 times; do not report it again this run"
    assert third["occurrences"] == 3
    assert "note" not in second, "a duplicate is accounted for, not lost — no 'not stored' note"


def test_a_different_tool_is_a_separate_row(api):
    flows_api, client = api
    _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL)
    r = _post(flows_api, client, tool="calendar_list", what_happened=REFUSAL).json()
    assert r["recorded"] is True
    assert _reactions(flows_api) == 2


def test_a_different_reason_is_a_separate_row(api):
    flows_api, client = api
    _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL)
    r = _post(flows_api, client, tool="gmail_search",
              what_happened='refused: {"reason": "opaque_limit"}').json()
    assert r["recorded"] is True
    r2 = _post(flows_api, client, tool="gmail_search", what_happened="the page was blank").json()
    assert r2["recorded"] is True
    assert _reactions(flows_api) == 3


def test_the_same_report_in_the_next_utc_hour_is_a_new_row(api):
    flows_api, client = api
    a = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    flows_api.clock.advance(3599)
    b = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    assert b["recorded"] is False and b["duplicate_of"] == a["id"]
    flows_api.clock.advance(1)                      # 11:00:00Z — the next bucket
    c = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    assert c["recorded"] is True and c["id"] != a["id"]
    assert _reactions(flows_api) == 2


def test_a_different_uid_never_increments_another_s_report(api):
    """Deny: one person's report is never folded into another person's row, and the second
    person's reply never names the first person's report id."""
    flows_api, client = api
    mine = _post(flows_api, client, uid="126", tool="gmail_search", what_happened=REFUSAL).json()
    theirs = _post(flows_api, client, uid="127", tool="gmail_search",
                   what_happened=REFUSAL).json()
    assert theirs["recorded"] is True
    assert "duplicate_of" not in theirs and theirs["id"] != mine["id"]
    assert _reactions(flows_api) == 2
    rows = flows_api.db.execute("SELECT uid, occurrences FROM friction_occurrence ORDER BY uid")
    assert [(u, int(n)) for u, n in rows] == [("126", 1), ("127", 1)]


def test_ids_and_timestamps_in_the_text_do_not_defeat_the_key(api):
    flows_api, client = api
    texts = [
        "draft 8f3a9c21d0 not found at 2026-10-10T10:01:02Z (request fr_0a1b2c3d4e5f6a7b)",
        "draft 77aa00bb11 not found at 2026-10-10T10:07:59.123Z (request fr_ffffeeee11112222)",
        "Draft 'robin.vale' not found at 2026-10-10 10:30:00 (request 4411)",
    ]
    for t in texts:
        _post(flows_api, client, tool="drafts_get", what_happened=t)
    assert _reactions(flows_api) == 1
    assert _reports(flows_api, client)[0]["occurrences"] == 3


def test_a_counter_whose_report_was_deleted_starts_over(api):
    """An operator removed the subject's reaction rows; a repeat must not be counted against a
    report nobody can read any more."""
    flows_api, client = api
    a = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    flows_api.db.execute("DELETE FROM reaction")
    b = _post(flows_api, client, tool="gmail_search", what_happened=REFUSAL).json()
    assert b["recorded"] is True and b["id"] != a["id"]
    assert _reports(flows_api, client)[0]["occurrences"] == 1


def test_a_report_filed_once_reads_occurrences_one(api):
    flows_api, client = api
    _post(flows_api, client, tool="x", what_happened="it broke")
    r = _reports(flows_api, client)[0]
    assert r["occurrences"] == 1 and r["last_seen"] == r["at"]


# ── the key itself ──────────────────────────────────────────────────────────────────────────────

def test_reason_key_prefers_the_refusal_token():
    assert friction_dedup.reason_key(REFUSAL) == "reason:human_session_required"
    assert friction_dedup.reason_key("refused with human_session_required again") \
        == "reason:human_session_required"
    assert friction_dedup.reason_key("reason=some_new_reason, nothing else") \
        == "reason:some_new_reason"


def test_reason_key_strips_ids_digits_hex_and_quotes():
    a = friction_dedup.reason_key("Meeting 1234 'Example Bank sync' failed: id 9f8e7d6c5b4a")
    b = friction_dedup.reason_key("meeting 98 \"Nora Quill 1:1\" FAILED:   id a1b2c3d4e5f6")
    assert a == b == "text:meeting failed id"


def test_reason_key_falls_back_to_what_i_tried_when_nothing_happened_is_given():
    assert friction_dedup.reason_key("", "open the inbox 3 times") == "text:open the inbox times"


def test_dedup_key_changes_with_each_part():
    base = dict(uid="126", tool="t", what_happened="x", what_i_tried="", now=HOUR_START)
    k = friction_dedup.dedup_key(**base)
    assert k == friction_dedup.dedup_key(**{**base, "now": HOUR_START + 3599})
    for change in ({"uid": "127"}, {"tool": "u"}, {"what_happened": "y"},
                   {"now": HOUR_START + 3600}):
        assert friction_dedup.dedup_key(**{**base, **change}) != k, change
