"""bot_send / bot_config — a gateway 409/404 forwarded raw used to mean ONE thing regardless of
what actually caused it (ledger F193/F194/F201, live agent + human witness, 2026-09-03):

  * a bot genuinely already in the call            → `already_there: true` was RIGHT
  * the previous bot for the same meeting still     → `already_there: true` was WRONG — the human
    tearing down ("stopping")                          twice mistook this for "the bot is in the
                                                          call" and never was
  * a stale 409 against a meeting that already        → `already_there: true` was WRONG — no bot
    ended (`completed`/`failed`)                        exists to talk to; a new one was never sent
  * bot_config's 404 carried no cause at all — "no bot ever existed", "it left" and "the meeting
    ended" all forwarded as a bare `{"applied": false, "status": 404}`

Fix: read meeting-api's actual state for the platform/native id before answering (`_bot_conflict_
state` — `/bots/status`, meeting-api's own non-terminal list, then `/meetings` for the terminal
answer) instead of inferring it from the HTTP status code alone.

No live gateway: `_gw_http` is monkeypatched directly to a scripted stub, because the shared `HTTP`
recorder in conftest.py answers one fixed response per URL fragment — these tests need the SAME
endpoint (`POST /bots`, `GET /bots/status`) to answer differently across the two calls a single
`bot_send` makes (the initial attempt, then the state read triggered by its 409).
"""
from __future__ import annotations

import json

from conftest import as_user, tool
import vexa_control_mcp as rig

MEETING_URL = "https://meet.google.com/abc-defg-hij"


def _script_gw_http(monkeypatch, script: dict):
    """`script[(method, path)]` is either one `(status, body)` reused for every call, or a list of
    them consumed in order (the last entry repeats once exhausted, so a test need not script a
    call it does not care about the tail of). Returns the list of `(method, path, body)` actually
    asked for, in order — what a test asserts the RETRY discipline on."""
    calls = []

    def fake(uid, method, path, body=None, timeout=40):
        calls.append((method, path, body))
        entry = script.get((method, path))
        if entry is None:
            return 200, {}
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry

    monkeypatch.setattr(rig, "_gw_http", fake)
    return calls


# ── bot_send: the three things a 409 can mean ──────────────────────────────────────────────────────

def test_409_with_a_live_bot_says_already_there_with_the_real_status(monkeypatch):
    as_user(monkeypatch, "7")
    calls = _script_gw_http(monkeypatch, {
        ("POST", "/bots"): (409, {}),
        ("GET", "/bots/status"): (200, {"running": [
            {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "status": "active"}]}),
    })
    out = json.loads(tool("bot_send")(meeting_url=MEETING_URL))
    assert out["already_there"] is True, out
    assert out["status"] == "active", out
    # ONE retry-worthy branch only: a live bot never gets a second POST /bots.
    assert [c[:2] for c in calls if c[0] == "POST"] == [("POST", "/bots")], calls


def test_409_with_the_previous_bot_still_leaving_is_not_reported_as_already_there(monkeypatch):
    """Live repro (F193): bot_stop reported `{"stopped": true}`, the very next bot_send returned
    `{"already_there": true}`, and bots_running() showed the meeting stuck in "stopping" — the
    same sentence a genuinely live bot gets, on a meeting nothing was in."""
    as_user(monkeypatch, "7")
    calls = _script_gw_http(monkeypatch, {
        ("POST", "/bots"): (409, {}),
        ("GET", "/bots/status"): (200, {"running": [
            {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "status": "stopping"}]}),
    })
    out = json.loads(tool("bot_send")(meeting_url=MEETING_URL))
    assert out.get("already_there") is False, out
    assert out["status"] == "stopping", out
    assert "leaving" in out.get("error", ""), out
    assert "again" in out.get("do", "").lower(), out
    assert [c[:2] for c in calls if c[0] == "POST"] == [("POST", "/bots")], calls


def test_409_against_an_already_ended_meeting_retries_once_and_succeeds(monkeypatch):
    """A stale 409: meeting-api has not reaped the terminal row yet. The retry is the whole fix —
    without it the caller was told a bot existed when none did and nothing was ever dispatched."""
    as_user(monkeypatch, "7")
    calls = _script_gw_http(monkeypatch, {
        ("POST", "/bots"): [(409, {}), (201, {"id": "row2", "status": "requested"})],
        ("GET", "/bots/status"): [
            (200, {"running": []}),  # the conflict read: nothing live
            (200, {"running": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                "status": "requested", "id": "row2"}]}),  # post-dispatch poll
        ],
        ("GET", "/meetings"): (200, {"meetings": [
            {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "status": "completed"}]}),
    })
    out = json.loads(tool("bot_send")(meeting_url=MEETING_URL))
    assert out.get("sent") is True, out
    assert out.get("meeting_row") == "row2", out
    assert out.get("bot_state") == "knocking", out
    assert [c[:2] for c in calls if c[0] == "POST"] == [("POST", "/bots"), ("POST", "/bots")], calls


def test_409_against_nothing_meeting_api_knows_reports_after_one_retry_not_a_loop(monkeypatch):
    as_user(monkeypatch, "7")
    calls = _script_gw_http(monkeypatch, {
        ("POST", "/bots"): (409, {}),  # every POST answers 409 — the retry must not loop forever
        ("GET", "/bots/status"): (200, {"running": []}),
        ("GET", "/meetings"): (200, {"meetings": []}),
    })
    out = json.loads(tool("bot_send")(meeting_url=MEETING_URL))
    assert "error" in out, out
    assert out["status"] == 409, out
    assert "unknown" in out["detail"], out
    assert [c[:2] for c in calls if c[0] == "POST"] == [("POST", "/bots"), ("POST", "/bots")], calls


# ── bot_config: a bare 404 names the cause ─────────────────────────────────────────────────────────

def test_bot_config_404_names_an_ended_meeting_as_the_cause(monkeypatch):
    as_user(monkeypatch, "7")
    _script_gw_http(monkeypatch, {
        ("PUT", "/bots/google_meet/abc-defg-hij/config"): (404, {}),
        ("GET", "/bots/status"): (200, {"running": []}),
        ("GET", "/meetings"): (200, {"meetings": [
            {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "status": "failed"}]}),
    })
    out = json.loads(tool("bot_config")(meeting_url=MEETING_URL, language="es"))
    assert out["applied"] is False, out
    assert "already ended" in out["error"], out
    assert "failed" in out["error"], out


def test_bot_config_404_with_no_known_state_still_names_the_cause_not_the_bare_code(monkeypatch):
    as_user(monkeypatch, "7")
    _script_gw_http(monkeypatch, {
        ("PUT", "/bots/google_meet/abc-defg-hij/config"): (404, {}),
        ("GET", "/bots/status"): (200, {"running": []}),
        ("GET", "/meetings"): (200, {"meetings": []}),
    })
    out = json.loads(tool("bot_config")(meeting_url=MEETING_URL, bot_name="Scribe"))
    assert out["applied"] is False, out
    assert "no bot to configure" in out["error"], out
    assert "status" not in out or out["status"] == 404


def test_bot_config_still_applies_normally(monkeypatch):
    as_user(monkeypatch, "7")
    _script_gw_http(monkeypatch, {
        ("PUT", "/bots/google_meet/abc-defg-hij/config"): (200, {}),
    })
    out = json.loads(tool("bot_config")(meeting_url=MEETING_URL, language="es"))
    assert out == {"applied": True, "status": 200}, out
