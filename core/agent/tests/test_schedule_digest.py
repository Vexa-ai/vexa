"""schedule_digest — the ambient terminal-state digest (context bundle, slice 1).

``build_schedule_digest`` is pure: rows → the ``<schedule>`` block, times in the caller's tz,
bounded sections, honest fallbacks. ``digest_source`` never raises (a digest must never fail a
chat turn) and caches per subject. ``find_row`` locates the focused meeting's server row for
the focus-enrichment fix.
"""
from __future__ import annotations

from datetime import datetime, timezone

from control_plane import schedule_digest as sd

NOW = datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc)  # 15:00 in Europe/Lisbon (WEST)


def _row(rid, status, *, title=None, scheduled_at=None, workspace_id=None, auto_join=None,
         native=None, platform="google_meet", end_time=None, processed=False):
    data = {}
    if title: data["title"] = title
    if scheduled_at: data["scheduled_at"] = scheduled_at
    if workspace_id: data["workspace_id"] = workspace_id
    if auto_join is not None: data["auto_join"] = auto_join
    if processed: data["processed"] = {"views": {"transcript": []}}
    return {"id": rid, "status": status, "platform": platform, "native_meeting_id": native,
            "data": data, "end_time": end_time, "start_time": None, "updated_at": None}


# ── build_schedule_digest ─────────────────────────────────────────────────────────────

def test_digest_buckets_and_renders_in_tz():
    rows = [
        _row(1, "active", title="Weekly sync", native="abc-defg-hij"),
        _row(2, "scheduled", title="Acme intro", scheduled_at="2026-07-08T16:00:00Z",
             workspace_id="acme-deal"),
        _row(3, "scheduled", title="Board review", scheduled_at="2026-07-10T09:00:00Z"),
        _row(4, "idle", title="Untimed plan"),
        _row(5, "completed", title="Design review", end_time="2026-07-07T15:00:00Z", processed=True),
    ]
    out = sd.build_schedule_digest(rows, tz="Europe/Lisbon", now=NOW)
    assert out.startswith('<schedule tz="Europe/Lisbon" now="Wed 2026-07-08 15:00">')
    assert 'live:\n- [meeting 1] "Weekly sync" (google_meet/abc-defg-hij) — bot active' in out
    assert '- 17:00 [meeting 2] "Acme intro" — prep workspace: acme-deal' in out  # 16Z = 17:00 WEST
    assert '- Fri 10 Jul 10:00 [meeting 3] "Board review"' in out
    assert '- unscheduled [meeting 4] "Untimed plan"' in out
    assert '"Design review" — notes ready' in out
    assert out.rstrip().endswith("</schedule>")


def test_digest_empty_rows_is_empty_string():
    assert sd.build_schedule_digest([], tz="Europe/Lisbon", now=NOW) == ""


def test_digest_invalid_tz_falls_back_to_utc():
    rows = [_row(2, "scheduled", title="X", scheduled_at="2026-07-08T16:00:00Z")]
    out = sd.build_schedule_digest(rows, tz="Not/AZone", now=NOW)
    assert "- 16:00 [meeting 2]" in out  # rendered in UTC


def test_digest_caps_sections():
    rows = [_row(i, "scheduled", title=f"m{i}", scheduled_at=f"2026-07-1{i % 8 + 1}T09:00:00Z")
            for i in range(20)]
    out = sd.build_schedule_digest(rows, now=NOW)
    assert out.count("[meeting ") == sd.MAX_UPCOMING


def test_digest_today_section_only_remaining_and_sorted():
    rows = [
        _row(1, "scheduled", title="later", scheduled_at="2026-07-08T18:00:00Z"),
        _row(2, "scheduled", title="sooner", scheduled_at="2026-07-08T15:00:00Z"),
        _row(3, "scheduled", title="this morning (past)", scheduled_at="2026-07-08T08:00:00Z"),
    ]
    out = sd.build_schedule_digest(rows, now=NOW)
    assert out.index('"sooner"') < out.index('"later"')
    assert "this morning" not in out  # past-today scheduled row is dropped (not full_day)


def test_digest_full_day_includes_ended_today_and_past_row():
    rows = [
        _row(1, "completed", title="standup", end_time="2026-07-08T09:00:00Z"),
        _row(2, "scheduled", title="next", scheduled_at="2026-07-08T16:00:00Z"),
    ]
    out = sd.build_schedule_digest(rows, now=NOW, full_day=True)
    assert "ended today:" in out and '"standup"' in out
    ambient = sd.build_schedule_digest(rows, now=NOW, full_day=False)
    assert "ended today:" not in ambient


def test_digest_title_truncated_and_char_capped():
    rows = [_row(i, "scheduled", title="T" * 200, scheduled_at="2026-07-09T09:00:00Z")
            for i in range(30)]
    out = sd.build_schedule_digest(rows, now=NOW)
    assert "T" * 61 not in out
    assert len(out) <= sd.DIGEST_CHAR_CAP + len("</schedule>\n\n") + 2
    assert out.rstrip().endswith("</schedule>")


def test_digest_failed_meeting_is_honest():
    rows = [_row(9, "failed", title="broken", end_time="2026-07-07T10:00:00Z")]
    out = sd.build_schedule_digest(rows, now=NOW)
    assert "bot failed — no record" in out


# ── digest_source (cache + degradation) ───────────────────────────────────────────────

def test_digest_source_caches_and_never_raises(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(url, uid, ws, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("meeting-api down")
        return [_row(1, "scheduled", title="ok", scheduled_at="2026-07-09T09:00:00Z")]

    monkeypatch.setattr(sd, "fetch_user_meetings", fake_fetch)
    t = {"v": 100.0}
    monkeypatch.setattr(sd.time, "monotonic", lambda: t["v"])
    src = sd.digest_source("http://meeting-api:8080", None, ttl_s=30.0)
    assert src("7") == []                      # failure → [] (no raise)
    t["v"] += 6.0                              # past the 5s fail cool-off
    rows = src("7")
    assert rows and rows[0]["data"]["title"] == "ok"
    t["v"] += 10.0                             # inside TTL → cached, no new call
    n = calls["n"]
    assert src("7") == rows and calls["n"] == n


def test_digest_source_membership_workspaces_forwarded(monkeypatch):
    seen = {}

    def fake_fetch(url, uid, ws, **kw):
        seen["ws"] = ws
        return []

    monkeypatch.setattr(sd, "fetch_user_meetings", fake_fetch)
    src = sd.digest_source("http://x", lambda s: [{"workspace_id": "w1"}, {"workspace_id": "w2"}])
    src("7")
    assert seen["ws"] == ["w1", "w2"]


# ── find_row (focus enrichment lookup) ────────────────────────────────────────────────

def test_find_row_by_id_then_native_prefers_active():
    rows = [
        _row(10, "completed", native="abc-defg-hij"),
        _row(11, "scheduled", native="abc-defg-hij"),
    ]
    assert sd.find_row(rows, meeting_id=10)["id"] == 10
    assert sd.find_row(rows, meeting_id="11")["id"] == 11
    assert sd.find_row(rows, native_id="abc-defg-hij")["id"] == 11  # non-terminal wins
    assert sd.find_row(rows, meeting_id=None, native_id="zzz") is None
