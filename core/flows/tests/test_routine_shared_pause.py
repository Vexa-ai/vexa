"""A routine whose chat became shared pauses on its first refused private read (Vexa-ai/vexa#1783,
routine-shared-thread-conflict).

A shared turn may use only what every participant can read (ADR-0048 decision 4), so a routine in a
chat somebody else joined is refused the owner's mail, calendar and meetings (`shared_conversation`)
on every run, and kept firing uselessly. Running it with the owner's private scope and filtering the
output is not on offer: there is no reliable leak filter for free-form agent output. So:

  * a run that is not refused runs as before, on the shared ceiling;
  * a run refused a private source BECAUSE the chat is shared ends refused (`failed`, never `done`),
    the routine pauses (its version is taken out of service, so it does not fire again), and its
    chat gets one line naming who it is shared with, what could not be read, and what to do;
  * the routine's card carries the reason (the strip shows it paused); resuming is the owner's act
    and clears it;
  * `_global/POLICIES.md` `routine_pauses_when_shared: off` keeps the old behaviour (the run fails
    with the phase-0 line and the routine stays on).
"""
from __future__ import annotations

import calendar
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from flows import FakeClock, Registry, schedule, tick  # noqa: E402
from flows.registry import SCHEDULE_EVENT  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://shared:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
    from flows_integrations import subject_auth  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

if not isinstance(fa.db, SqliteDB):
    fa.db = SqliteDB()

T = float(calendar.timegm((2027, 3, 1, 12, 10, 0)))
NAME = "morning-brief"


def _pol():
    import importlib
    return importlib.import_module("flows_steps.policies")


class _AgentApi:
    def __init__(self, outcome):
        self.outcome = outcome
        self.posts, self.notices = [], []

    def __call__(self, method, url, headers, body=None, timeout=20):
        if method == "GET" and "/api/chat/pending" in url:
            return 200, {"pending": [], "busy": False, "routine_cursor": "17-0"}
        if method == "GET" and "/api/chat/turn-outcome" in url:
            return 200, self.outcome
        if method == "POST" and url.endswith("/api/chat/submit"):
            if headers.get("X-Vexa-Flow-Step") == "notice":
                self.notices.append(body["prompt"])
            else:
                self.posts.append(dict(body))
            return 200, {"ok": True, "unit": "u"}
        raise AssertionError(f"unexpected call {method} {url}")


def _row(db):
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
               "status, created_by, created_at) VALUES ('user:126', :n, 1, :e, '0 * * * *', :s, "
               ":p, 'active', 't', :t)",
               {"n": NAME, "e": SCHEDULE_EVENT, "s": json.dumps(["agent_run"]),
                "p": json.dumps({"instruction": "summarise my inbox", "thread": "pchat-abc"}),
                "t": T - 600})


def _run(monkeypatch, outcome, rules=None, db=None):
    from flows_steps import common, routine
    api = _AgentApi(outcome)
    monkeypatch.setattr(routine, "http", api)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    pol = _pol()
    monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(rules))
    db = db or SqliteDB()
    clock, reg = FakeClock(T), Registry()
    routine.register(reg, db)
    _row(db)
    reg.refresh_from_db(db)
    schedule.fire_due(db, reg, clock)
    for _ in range(6):
        while tick(db, reg, clock):
            pass
        clock.advance(20)
    status, reason = db.execute("SELECT status, reason FROM reaction WHERE event_type = :e",
                                {"e": SCHEDULE_EVENT})[0]
    return api, db, clock, reg, status, reason


SHARED = {"state": "done", "ok": True, "acted": [], "failed": ["mcp__vexa__gmail_search"],
          "refused": {"tool": "mcp__vexa__gmail_search", "reason": "shared_conversation", "count": 2},
          "shared_with": ["Bob"]}


def _version(db):
    status, params = db.execute("SELECT status, params FROM flow_version WHERE name = :n",
                                {"n": NAME})[0]
    return status, json.loads(params or "{}")


def test_a_run_refused_a_private_source_in_a_shared_chat_ends_refused_and_pauses(monkeypatch):
    api, db, clock, reg, status, reason = _run(monkeypatch, SHARED)
    assert status == "failed" and "shared_conversation" in reason
    assert len(api.notices) == 1
    line = api.notices[0]
    assert line.startswith("Paused: this chat is now shared with Bob")
    assert "your mail can't be read here" in line
    assert "Unshare the chat, or move the routine to a private chat, then resume." in line
    vstatus, params = _version(db)
    assert vstatus == "retired", "paused: the version is out of service"
    assert params["paused"]["reason"] == "shared_conversation"
    assert params["paused"]["text"].startswith("this chat is now shared with Bob")
    # …and it is not fired again while paused
    reg.refresh_from_db(db)
    clock.advance(3600)
    assert schedule.fire_due(db, reg, clock) == 0


def test_a_run_that_is_not_refused_keeps_running_on_the_shared_ceiling(monkeypatch):
    ok = {"state": "done", "ok": True, "refused": None, "acted": ["workspace_read"], "failed": [],
          "shared_with": ["Bob"]}
    api, db, *_rest, status, _ = _run(monkeypatch, ok)
    assert status == "done" and not api.notices
    assert _version(db)[0] == "active"


def test_another_refusal_does_not_pause(monkeypatch):
    other = {**SHARED, "refused": {"tool": "request_meeting_bot", "reason": "human_session_required",
                                   "count": 1}}
    api, db, *_rest, status, _ = _run(monkeypatch, other)
    assert status == "failed" and _version(db)[0] == "active"
    assert not api.notices[0].startswith("Paused")


def test_the_policy_may_keep_it_running(monkeypatch):
    api, db, *_rest, status, _ = _run(monkeypatch, SHARED,
                                      rules="---\nroutine_pauses_when_shared: off\n---\n")
    assert status == "failed" and _version(db)[0] == "active"
    assert not api.notices[0].startswith("Paused")


def test_names_and_sources_read_as_words():
    from flows_steps import routine
    assert routine.shared_pause_text(["Bob", "Carla", "Dan"], "mcp__vexa__calendar_list").startswith(
        "this chat is now shared with Bob, Carla and Dan, so your calendar can't be read here.")
    assert "shared with other people, so your meetings" in routine.shared_pause_text(
        [], "get_meeting_transcript")


# ── the owner resumes it, through flows-api, and the reason goes with the pause ─────────────────

@pytest.fixture
def client(monkeypatch):
    for t in ("flow_version", "effect_receipt", "reaction"):
        fa.db.execute(f"DELETE FROM {t}")
    pol = _pol()
    monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(None))
    monkeypatch.setattr(fa.clock, "now", lambda: T)
    table = {"anna-key": (200, {"user_id": 126, "email": "anna@vexa.test", "credential_class": "session"})}
    monkeypatch.setattr(subject_auth, "_validate", lambda base, token, secret: table.get(token, (401, {})))
    return TestClient(fa.app, raise_server_exceptions=False)


def test_resuming_is_the_owners_act_and_clears_the_reason(client):
    _row(fa.db)
    fa.db.execute("UPDATE flow_version SET status='retired', params=:p WHERE name=:n",
                  {"n": NAME, "p": json.dumps({"instruction": "x", "thread": "pchat-abc",
                                               "paused": {"reason": "shared_conversation",
                                                          "text": "this chat is now shared"}})})
    listed = client.get("/flows", headers={"Authorization": "Bearer anna-key"}).json()["flows"]
    mine = next(f for f in listed if f["name"] == NAME)
    assert mine["paused_reason"] == "this chat is now shared"
    r = client.post(f"/flows/{NAME}/1/resume?scope=user:126",
                    headers={"Authorization": "Bearer anna-key"})
    assert r.status_code == 200, r.text
    status, params = _version(fa.db)
    assert status == "active" and "paused" not in params
