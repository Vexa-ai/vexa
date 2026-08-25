"""``GET /meetings/completion-summary`` — how an account's meetings are ENDING (#1292).

Drives the shipped ``create_app`` over the in-memory fake, OFFLINE (TestClient, no docker, no DB).

The per-meeting ``completion_reason`` has shipped since v0.12.16; the aggregate had not, so a caller
asking *"why do my bots keep failing?"* had to page their whole history and tally it client-side.

Four properties get their own tests because each one, if it broke, would break QUIETLY — the response
would still look like a plausible summary:

  * the account boundary (another user's meetings must not be counted, nor shared-to-me ones);
  * the ``unrecorded`` bucket (terminal-with-no-reason must be NAMED, never dropped — a silent drop
    makes the buckets under-sum and the caller mis-attributes the shortfall);
  * the terminal filter (an in-flight run has no reason YET, which is not the same as having none);
  * **no rate anywhere in the body** — the regression this whole endpoint exists to prevent.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import build_completion_summary

USER = 7
OTHER = 99
GATEWAY_HEADERS = {"x-user-id": str(USER)}


def _store_with(rows, *, user_id=USER):
    """Seed ``(status, completion_reason, created_at)`` triples for one user."""
    store = InMemoryTranscriptStore()
    for i, (status, reason, created) in enumerate(rows):
        data = {} if reason is None else {"completion_reason": reason}
        store.seed_meeting(
            user_id=user_id, platform="google_meet", native_meeting_id=f"m-{user_id}-{i}",
            status=status, data=data, created_at=created,
        )
    return store


def _get(store, query=""):
    client = TestClient(create_app(store, redis=None))
    r = client.get(f"/meetings/completion-summary{query}", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def test_counts_terminal_meetings_by_completion_reason():
    store = _store_with([
        ("completed", "stopped", "2026-08-01T09:00:00Z"),
        ("completed", "stopped", "2026-08-02T09:00:00Z"),
        ("completed", "left_alone", "2026-08-03T09:00:00Z"),
        ("failed", "join_failure", "2026-08-04T09:00:00Z"),
    ])
    body = _get(store)
    assert body["total"] == 4
    assert body["by_status"] == {"completed": 3, "failed": 1}
    assert body["by_completion_reason"] == {"stopped": 2, "left_alone": 1, "join_failure": 1}


def test_reason_buckets_are_ordered_by_count_descending():
    """Ties break alphabetically, so two identical accounts produce byte-identical bodies."""
    store = _store_with([
        ("completed", "left_alone", "2026-08-01T09:00:00Z"),
        ("completed", "stopped", "2026-08-02T09:00:00Z"),
        ("completed", "stopped", "2026-08-03T09:00:00Z"),
        ("completed", "evicted", "2026-08-04T09:00:00Z"),
    ])
    assert list(_get(store)["by_completion_reason"]) == ["stopped", "evicted", "left_alone"]


def test_terminal_meeting_with_no_reason_is_named_unrecorded_not_dropped():
    """The buckets must SUM to the terminal count.

    Dropping a reasonless row would make the summary under-report without saying so — and a caller
    who notices the shortfall has no way to tell a missing reason from a missing meeting.
    """
    store = _store_with([
        ("completed", "stopped", "2026-08-01T09:00:00Z"),
        ("completed", None, "2026-08-02T09:00:00Z"),
        ("failed", None, "2026-08-03T09:00:00Z"),
    ])
    body = _get(store)
    assert body["by_completion_reason"] == {"unrecorded": 2, "stopped": 1}
    terminal = body["by_status"]["completed"] + body["by_status"]["failed"]
    assert sum(body["by_completion_reason"].values()) == terminal


def test_in_flight_meetings_are_counted_by_status_but_carry_no_reason():
    store = _store_with([
        ("completed", "stopped", "2026-08-01T09:00:00Z"),
        ("active", None, "2026-08-02T09:00:00Z"),
        ("requested", None, "2026-08-03T09:00:00Z"),
        ("awaiting_admission", None, "2026-08-04T09:00:00Z"),
    ])
    body = _get(store)
    assert body["total"] == 4
    assert body["by_status"]["active"] == 1
    assert body["by_status"]["awaiting_admission"] == 1
    # Not bucketed as `unrecorded`: they have no reason YET, which is a different fact.
    assert body["by_completion_reason"] == {"stopped": 1}


def test_another_accounts_meetings_are_never_counted():
    store = _store_with([("completed", "stopped", "2026-08-01T09:00:00Z")])
    for i in range(5):
        store.seed_meeting(
            user_id=OTHER, platform="google_meet", native_meeting_id=f"other-{i}",
            status="failed", data={"completion_reason": "join_failure"},
            created_at="2026-08-02T09:00:00Z",
        )
    body = _get(store)
    assert body["total"] == 1
    assert "join_failure" not in body["by_completion_reason"]


def test_a_meeting_shared_to_me_is_not_my_meeting():
    """`list_meetings` returns shared rows; this endpoint deliberately does NOT count them.

    Someone else's bot run says nothing about how MY meetings end, and folding it in would leak the
    shape of their reliability into my summary.
    """
    store = _store_with([("completed", "stopped", "2026-08-01T09:00:00Z")])
    store.seed_meeting(
        user_id=OTHER, platform="google_meet", native_meeting_id="shared-to-me",
        status="failed",
        data={"completion_reason": "join_failure", "transcript_viewers": [USER]},
        created_at="2026-08-02T09:00:00Z",
    )
    body = _get(store)
    assert body["total"] == 1
    assert body["by_completion_reason"] == {"stopped": 1}


def test_window_is_half_open_so_adjacent_windows_tile_without_double_counting():
    store = _store_with([
        ("completed", "stopped", "2026-07-31T23:59:59Z"),
        ("completed", "left_alone", "2026-08-01T00:00:00Z"),
        ("completed", "evicted", "2026-08-31T12:00:00Z"),
    ])
    august = _get(store, "?since=2026-08-01T00:00:00Z&until=2026-09-01T00:00:00Z")
    assert august["total"] == 2
    assert august["by_completion_reason"] == {"evicted": 1, "left_alone": 1}
    # The boundary row belongs to July alone — counted once across the two windows, not twice.
    july = _get(store, "?since=2026-07-01T00:00:00Z&until=2026-08-01T00:00:00Z")
    assert july["by_completion_reason"] == {"stopped": 1}


def test_window_echoes_the_bound_actually_applied():
    store = _store_with([("completed", "stopped", "2026-08-01T09:00:00Z")])
    body = _get(store, "?since=2026-08-01T00:00:00Z")
    assert body["window"] == {"since": "2026-08-01T00:00:00Z", "until": None}


def test_an_unparseable_bound_is_ignored_and_echoed_as_null():
    """Degrade to unbounded rather than 500 — and SAY so, so the caller can see it was dropped."""
    store = _store_with([("completed", "stopped", "2026-08-01T09:00:00Z")])
    body = _get(store, "?since=last-tuesday")
    assert body["total"] == 1
    assert body["window"]["since"] is None


def test_platform_filter_narrows_both_tallies():
    store = _store_with([("completed", "stopped", "2026-08-01T09:00:00Z")])
    store.seed_meeting(
        user_id=USER, platform="teams", native_meeting_id="t-1", status="failed",
        data={"completion_reason": "join_failure"}, created_at="2026-08-02T09:00:00Z",
    )
    body = _get(store, "?platform=teams")
    assert body["total"] == 1
    assert body["by_status"] == {"failed": 1}
    assert body["by_completion_reason"] == {"join_failure": 1}


def test_an_empty_account_gets_zeroed_buckets_not_a_404():
    body = _get(InMemoryTranscriptStore())
    assert body["total"] == 0
    assert body["by_status"] == {}
    assert body["by_completion_reason"] == {}


def test_a_reason_outside_the_sealed_enum_is_reported_as_recorded():
    """``bot_spawn/adapters.py`` writes ``start_failed``, which is in NEITHER sealed enum.

    Normalising it at the read edge would manufacture conformance and hide the writer's defect from
    the one surface able to reveal it. Surfacing it is the point.
    """
    store = _store_with([
        ("failed", "start_failed", "2026-08-01T09:00:00Z"),
        ("completed", "stopped", "2026-08-02T09:00:00Z"),
    ])
    assert _get(store)["by_completion_reason"]["start_failed"] == 1


def test_the_body_publishes_no_rate_ratio_or_percentage():
    """THE regression guard.

    Ten reasons are not commensurable — a bot the user stopped, a meeting nobody attended and a bot
    that could not join are all "not completed" and mean opposite things; only `join_failure` and
    `auth_session_missing` are our software breaking. A ratio over them invites the wrong denominator,
    which is the exact mistake this endpoint exists to stop anyone making. Counts only, forever.
    """
    store = _store_with([
        ("completed", "stopped", "2026-08-01T09:00:00Z"),
        ("failed", "join_failure", "2026-08-02T09:00:00Z"),
    ])
    body = _get(store)

    banned = ("rate", "ratio", "percent", "pct", "share", "failure_rate", "success")
    def _walk(node, path="body"):
        if isinstance(node, dict):
            for k, v in node.items():
                assert not any(b in str(k).lower() for b in banned), f"{path}.{k} looks like a rate"
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        else:
            # No fractional value can appear: every leaf is a count, a timestamp or null.
            assert not isinstance(node, float), f"{path} is a float — counts are integers"
    _walk(body)


def test_builder_is_pure_and_folds_none_into_unrecorded():
    """The shaping the SQL adapter and the fake SHARE, exercised without either."""
    counts = {None: 2, "stopped": 1}
    out = build_completion_summary({"completed": 3}, counts, since=None, until=None)
    assert out["by_completion_reason"] == {"unrecorded": 2, "stopped": 1}
    assert out["total"] == 3
    # Non-mutating: the caller's tally is untouched.
    assert counts == {None: 2, "stopped": 1}
