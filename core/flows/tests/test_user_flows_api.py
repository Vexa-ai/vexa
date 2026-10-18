"""ADR-0046 decisions 5, 6, 9, 10 at flows-api's door: who may write which scope, the step
allow-list, the quotas, and that a person reads and steers only their own flows and runs.

Same offline harness as `test_subject_bearer.py`: identity stubbed at its one HTTP hop, the DB the
sqlite double."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://user-flows:unreachable@127.0.0.1:1/flows"}
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

ROUTINE = {"name": "brief", "trigger": {"kind": "schedule", "cron": "0 7 * * 1-5"},
           "steps": ["agent_run"], "params": {"instruction": "brief me"}}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    fa.db.execute("DELETE FROM flow_version")
    fa.db.execute("DELETE FROM effect_receipt")
    fa.db.execute("DELETE FROM reaction")
    # no `_global` reachable offline: the policy is the image's marks unless a test says otherwise
    from flows_steps import policies
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(None))
    table = {
        "anna-key": (200, {"user_id": 126, "email": "anna@vexa.test"}),
        "ben-key": (200, {"user_id": 512, "email": "ben@vexa.test"}),
        "root-key": (200, {"user_id": 1, "email": "root@vexa.test", "admin_account": True}),
        "vxd_auto": (200, {"user_id": 126, "email": "anna@vexa.test", "is_admin": False,
                           "delegation": {"regime": "autonomous", "workspaces": ["126"]}}),
        # the admin account's own chat: identity verified the delegation and answers, for its person,
        # `person_admin_account` — and `admin_account: False`, since a worker is never the account
        "vxd_root_chat": (200, {"user_id": 1, "email": "root@vexa.test", "admin_account": False,
                                "person_admin_account": True,
                                "delegation": {"regime": "human", "workspaces": "*"}}),
        # an everyday account's chat
        "vxd_anna_chat": (200, {"user_id": 126, "email": "anna@vexa.test", "admin_account": False,
                                "person_admin_account": False,
                                "delegation": {"regime": "human", "workspaces": "*"}}),
        # a worker token whose answer lost its delegation: never the admin account, whatever it says
        "vxd_lost": (200, {"user_id": 1, "email": "root@vexa.test", "admin_account": True,
                           "person_admin_account": True}),
    }
    monkeypatch.setattr(subject_auth, "_validate",
                        lambda base, token, secret: table.get(token, (401, {})))
    yield


@pytest.fixture
def client():
    return TestClient(fa.app, raise_server_exceptions=False)


def as_(token):
    return {"Authorization": f"Bearer {token}"}


OPERATOR = {"X-Flows-Operator-Key": fa.API_KEY}


# ── decision 5: the scope comes from the caller ──────────────────────────────────────────────────

def test_a_persons_flow_is_written_in_their_own_scope(client):
    r = client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    assert r.status_code == 201, r.text
    assert r.json()["scope"] == "user:126" and r.json()["trigger"]["cron"] == "0 7 * * 1-5"
    row = fa.db.execute("SELECT scope, cron, on_event FROM flow_version")[0]
    assert row == ("user:126", "0 7 * * 1-5", "schedule.due")


def test_nobody_names_another_persons_scope(client):
    r = client.post("/flows", json={**ROUTINE, "scope": "user:512"}, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_scope"


def test_only_the_instance_admin_or_the_operator_writes_the_instance(client):
    body = {**ROUTINE, "scope": "instance", "steps": ["agent_run"]}
    assert client.post("/flows", json=body, headers=as_("anna-key")).status_code == 403
    assert client.post("/flows", json=body, headers=as_("root-key")).json()["scope"] == "instance"
    assert client.post("/flows", json=body, headers=OPERATOR).json()["scope"] == "instance"


def test_the_admin_accounts_chat_writes_the_instance(client):
    """Founder ruling 2026-10-11: the admin account sets up the company's flows from its chat, over
    the MCP, with its own delegated credential — its person bit is identity's, read from the
    verified delegation, never from anything the worker sent."""
    body = {**ROUTINE, "scope": "instance", "steps": ["agent_run"]}
    r = client.post("/flows", json=body, headers=as_("vxd_root_chat"))
    assert r.status_code == 201, r.text
    assert r.json()["scope"] == "instance"
    r = client.post("/flows/brief/1/pause", params={"scope": "instance"},
                    headers=as_("vxd_root_chat"))
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "instance"


def test_an_everyday_accounts_chat_is_refused_the_instance(client):
    body = {**ROUTINE, "scope": "instance", "steps": ["agent_run"]}
    r = client.post("/flows", json=body, headers=as_("vxd_anna_chat"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_scope"
    assert client.post("/flows", json=ROUTINE, headers=as_("vxd_anna_chat")).json()["scope"] \
        == "user:126"
    client.post("/flows", json=body, headers=OPERATOR)
    r = client.post("/flows/brief/1/retire", params={"scope": "instance"},
                    headers=as_("vxd_anna_chat"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_flow"


def test_an_everyday_account_is_refused_the_instance(client):
    body = {**ROUTINE, "scope": "instance", "steps": ["agent_run"]}
    r = client.post("/flows", json=body, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_scope"


def test_a_worker_answer_without_its_delegation_is_never_the_admin_account(client):
    body = {**ROUTINE, "scope": "instance", "steps": ["agent_run"]}
    r = client.post("/flows", json=body, headers=as_("vxd_lost"))
    assert r.status_code == 403


def test_the_operator_may_write_for_a_named_person(client):
    r = client.post("/flows", json={**ROUTINE, "scope": "user:512"}, headers=OPERATOR)
    assert r.status_code == 201 and r.json()["scope"] == "user:512"


def test_a_worker_with_nobody_in_the_loop_cannot_author_a_flow(client):
    r = client.post("/flows", json=ROUTINE, headers=as_("vxd_auto"))
    assert r.status_code == 403 and r.json()["detail"] == subject_auth.REFUSAL


def test_versions_are_per_scope(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    client.post("/flows", json=ROUTINE, headers=as_("ben-key"))
    r = client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    assert r.json()["version"] == 2
    assert fa.db.execute("SELECT COUNT(*) FROM flow_version WHERE scope='user:512'")[0][0] == 1


# ── decision 6: the step allow-list ──────────────────────────────────────────────────────────────

def test_a_step_a_person_may_not_use_is_refused_by_name(client):
    r = client.post("/flows", json={**ROUTINE, "steps": ["agent_run", "email_attendees"]},
                    headers=as_("anna-key"))
    assert r.status_code == 403
    d = r.json()["detail"]
    assert d["refused"] == "step_not_allowed" and d["step"] == "email_attendees"


def test_the_policy_file_widens_the_list(client, monkeypatch):
    from flows_steps import policies
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(
        "---\nuser_flow_steps: agent_run, email_attendees\n---\n"))
    r = client.post("/flows", json={**ROUTINE, "steps": ["agent_run", "email_attendees"]},
                    headers=as_("anna-key"))
    assert r.status_code == 201, r.text


def test_the_policy_file_narrows_the_list(client, monkeypatch):
    from flows_steps import policies
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(
        "---\nuser_flow_steps: notify_only_this\n---\n"))
    r = client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["step"] == "agent_run"


def test_agent_run_needs_an_instruction(client):
    r = client.post("/flows", json={**ROUTINE, "params": {}}, headers=as_("anna-key"))
    assert r.status_code == 400 and r.json()["detail"]["refused"] == "instruction_required"


def test_a_bad_cron_and_the_reserved_event_are_refused(client):
    bad = client.post("/flows", json={**ROUTINE, "trigger": {"kind": "schedule", "cron": "7"}},
                      headers=as_("anna-key"))
    assert bad.json()["detail"]["refused"] == "bad_cron"
    res = client.post("/flows", json={**ROUTINE, "trigger": {"kind": "event",
                                                             "event": "schedule.due"}},
                      headers=as_("anna-key"))
    assert res.json()["detail"]["refused"] == "reserved_event"


# ── decision 9: quotas ───────────────────────────────────────────────────────────────────────────

def test_a_schedule_that_fires_too_often_is_refused(client):
    r = client.post("/flows", json={**ROUTINE, "trigger": {"kind": "schedule",
                                                           "cron": "*/5 * * * *"}},
                    headers=as_("anna-key"))
    assert r.status_code == 403
    d = r.json()["detail"]
    assert d["refused"] == "quota_min_interval" and d["interval_s"] == 300


def test_active_flows_are_capped_per_person(client, monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_USER_ACTIVE_FLOWS", "2")
    for n in ("a", "b"):
        assert client.post("/flows", json={**ROUTINE, "name": n},
                           headers=as_("anna-key")).status_code == 201
    r = client.post("/flows", json={**ROUTINE, "name": "c"}, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "quota_active_flows"
    # a new version of one already active is not a new flow
    assert client.post("/flows", json={**ROUTINE, "name": "a"},
                       headers=as_("anna-key")).status_code == 201
    # and another person's count is their own
    assert client.post("/flows", json={**ROUTINE, "name": "c"},
                       headers=as_("ben-key")).status_code == 201


# ── reads, lifecycle and runs check ownership ────────────────────────────────────────────────────

def test_a_person_lists_the_instances_flows_and_their_own_never_anothers(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    client.post("/flows", json={**ROUTINE, "name": "bens"}, headers=as_("ben-key"))
    names = {(f["scope"], f["name"]) for f in client.get("/flows", headers=as_("anna-key")).json()[
        "flows"]}
    assert ("user:126", "brief") in names and not any(s == "user:512" for s, _ in names)
    vocab = {s["name"]: s["user_allowed"]
             for s in client.get("/flows", headers=as_("anna-key")).json()["steps_vocabulary"]}
    assert vocab["agent_run"] is True and vocab["email_attendees"] is False


def test_a_person_pauses_and_resumes_their_own_flow_and_nobody_elses(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    r = client.post("/flows/brief/1/pause", headers=as_("anna-key"))
    assert r.status_code == 200 and r.json()["status"] == "retired"
    assert client.post("/flows/brief/1/resume", headers=as_("anna-key")).json()["status"] == "active"
    other = client.post("/flows/brief/1/retire", params={"scope": "user:126"},
                        headers=as_("ben-key"))
    assert other.status_code == 403 and other.json()["detail"]["refused"] == "not_your_flow"
    assert client.post("/flows/brief/1/retire", headers=as_("ben-key")).status_code == 404


def test_run_history_is_the_callers_own(client):
    for rid, flow in (("r1", "user:126/brief"), ("r2", "user:512/brief"), ("r3", "post_meeting")):
        fa.db.execute(
            "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow, "
            "flow_version, step, status, attempt, next_run_at, created_at, updated_at) VALUES "
            "(:r, :r, 'schedule.due', '{}', :f, 1, 'agent_run', 'done', 0, 0, 1, 1)",
            {"r": rid, "f": flow})
    fa.db.execute("INSERT INTO effect_receipt (effect_key, reaction_id, step, state, result, "
                  "attempted_at) VALUES ('r1:agent_run', 'r1', 'agent_run', 'confirmed', "
                  ":res, 1)", {"res": json.dumps({"session": "flow-x"})})
    runs = client.get("/flows/runs", headers=as_("anna-key")).json()["runs"]
    assert [r["id"] for r in runs] == ["r1"]
    assert runs[0]["flow"] == "brief" and runs[0]["receipts"][0]["result"] == {"session": "flow-x"}
    assert client.get("/flows/runs", headers=OPERATOR).status_code == 400, "the operator names one"


def test_nobody_publishes_the_schedule_event(client):
    r = client.post("/events", json={"event_type": "schedule.due", "source_event_id": "x",
                                     "refs": {}}, headers=OPERATOR)
    assert r.status_code == 400


def test_a_persons_flow_pages_never_join_the_instances(client):
    """`_global/flows/` is written from the instance answer and every person reads it."""
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    inst = client.get("/flows/pages", params={"bodies": 0}, headers=OPERATOR).json()
    assert inst["scope"] == "instance" and inst["pages"] == []
    mine = client.get("/flows/pages", params={"bodies": 0, "scope": "user:126"},
                      headers=as_("anna-key")).json()
    assert [p["flow"] for p in mine["pages"]] == ["brief"]
    other = client.get("/flows/pages", params={"scope": "user:126"}, headers=as_("ben-key"))
    assert other.status_code == 403


# ── one active version per flow ─────────────────────────────────────────────────────────────────

def _active(scope, name):
    return [v for (v,) in fa.db.execute(
        "SELECT version FROM flow_version WHERE scope=:sc AND name=:n AND status='active' "
        "ORDER BY version", {"sc": scope, "n": name})]


def test_a_new_version_supersedes_the_one_running(client):
    """Submitting a flow again made a second ACTIVE version beside the first, and a schedule fired
    once per active version — six copies of one routine ran every 15 minutes on app.dev."""
    for _ in range(3):
        assert client.post("/flows", json=ROUTINE, headers=as_("anna-key")).status_code == 201
    assert _active("user:126", "brief") == [3]
    client.post("/flows", json=ROUTINE, headers=as_("ben-key"))
    assert _active("user:512", "brief") == [1] and _active("user:126", "brief") == [3], \
        "another scope's flow of the same name is untouched"


def test_a_draft_does_not_take_the_running_version_out(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    client.post("/flows", json={**ROUTINE, "activate": False}, headers=as_("anna-key"))
    assert _active("user:126", "brief") == [1]


def test_resuming_an_older_version_makes_it_the_only_one(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    assert client.post("/flows/brief/1/resume", headers=as_("anna-key")).json()["status"] == "active"
    assert _active("user:126", "brief") == [1]


def test_a_persons_flow_says_why_it_did_not_run(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    now = fa.clock.now()
    fa.db.execute(
        "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow, "
        "flow_version, step, status, attempt, next_run_at, reason, created_at, updated_at) VALUES "
        "('r1', 'refused:user:126/brief:quota_runs_per_day:d', 'schedule.refused', :refs, "
        "'user:126/brief', 1, 'agent_run', 'failed', 0, :t, 'quota_runs_per_day: x', :t, :t)",
        {"refs": json.dumps({"refused": "quota_runs_per_day", "text": "Not run: daily limit",
                             "next_run_at": now + 600, "uid": "126"}), "t": now})
    mine = [f for f in client.get("/flows", headers=as_("anna-key")).json()["flows"]
            if f["name"] == "brief" and f["scope"] == "user:126"]
    assert mine[0]["last_refusal"]["reason"] == "quota_runs_per_day"
    assert mine[0]["last_refusal"]["next_run_at"] == now + 600


def test_a_persons_flow_carries_its_last_run_and_todays_count(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    now = fa.clock.now()
    fa.db.execute(
        "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow, "
        "flow_version, step, status, attempt, next_run_at, created_at, updated_at) VALUES "
        "('r2', 's1', 'schedule.due', '{}', 'user:126/brief', 1, 'agent_run', 'done', 0, :t, :t, :u)",
        {"t": now - 60, "u": now - 19})
    fa.db.execute("INSERT INTO effect_receipt (effect_key, reaction_id, step, state, attempted_at, result) "
                  "VALUES ('k1', 'r2', 'agent_run', 'confirmed', :a, :res)",
                  {"a": now - 20, "res": json.dumps({"session": "meet-86"})})
    mine = next(f for f in client.get("/flows", headers=as_("anna-key")).json()["flows"]
                if f["name"] == "brief" and f["scope"] == "user:126")
    assert mine["runs_today"] == 1 and mine["runs_per_day"] > 0
    run = mine["last_run"]
    assert run["status"] == "done" and run["duration_s"] == 41.0 and run["session"] == "meet-86"
    assert run["steps"]["agent_run"]["state"] == "confirmed"


# ── claiming a routine for a chat: one operation, no new version ────────────────────────────────

LISBON = {"name": "lisbon-flights", "trigger": {"kind": "schedule", "cron": "*/30 * * * *"},
          "steps": ["agent_run"], "params": {"instruction": "Report Lisbon flights.", "thread": "pchat-old"}}


def test_claim_lisbon_flights_routine_here(client):
    """The founder's exchange: "claim lisbon flights routine here". It used to re-submit (a v3 beside a
    v2 still running) and the agent said v2 "should have stopped… but I didn't check"."""
    client.post("/flows", json=LISBON, headers=as_("anna-key"))
    client.post("/flows", json=LISBON, headers=as_("anna-key"))           # v2, the one running
    r = client.post("/flows/lisbon-flights/claim", json={"thread": "meet-86"}, headers=as_("anna-key"))
    assert r.status_code == 200, r.text
    facts = r.json()
    assert facts["active"] == 2 and facts["active_versions"] == 1, "no new version, one running"
    assert facts["thread"] == "meet-86" and facts["moved_from"] == "pchat-old"
    assert facts["trigger"] == {"kind": "schedule", "cron": "*/30 * * * *"} and facts["next_run_at"]
    rows = fa.db.execute("SELECT version, status, cron FROM flow_version WHERE name='lisbon-flights' "
                         "ORDER BY version")
    assert rows == [(1, "retired", "*/30 * * * *"), (2, "active", "*/30 * * * *")], "schedule unchanged"


def test_claim_is_yours_only_and_needs_a_running_routine(client):
    client.post("/flows", json=LISBON, headers=as_("anna-key"))
    assert client.post("/flows/lisbon-flights/claim", json={"thread": "x"},
                       headers=as_("ben-key")).status_code == 404
    assert client.post("/flows/nope/claim", json={"thread": "x"},
                       headers=as_("anna-key")).json()["detail"]["refused"] == "no_active_routine"
    assert client.post("/flows/lisbon-flights/claim", json={"thread": "x"},
                       headers=as_("vxd_auto")).status_code == 403


def test_a_chat_takes_on_another_timer_but_never_an_event_as_a_routine(client):
    r = client.post("/routines/triggers", headers=as_("anna-key"), json={
        "thread": "meet-86", "name": "prod-check", "trigger": {"kind": "schedule", "cron": "*/15 * * * *"},
        "instruction": "Check production; say nothing when nothing changed."})
    assert r.status_code == 201 and r.json()["thread"] == "meet-86" and r.json()["active"] == 1
    r = client.post("/routines/triggers", headers=as_("anna-key"), json={
        "thread": "meet-86", "name": "on-alert", "trigger": {"kind": "event", "event": "alert.fired"},
        "instruction": "x"})
    assert r.status_code == 422 and r.json()["detail"]["refused"] == "routine_needs_schedule"



def test_the_kill_switch_pauses_event_agents_and_leaves_routines(client):
    client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    client.post("/flows", json={"name": "follow-up", "trigger": {"kind": "event", "event": "meeting.completed"},
                                "steps": ["agent_run"], "params": {"instruction": "x", "thread": "per:meeting_id"}},
                headers=as_("anna-key"))
    client.post("/flows", json={"name": "follow-up", "trigger": {"kind": "event", "event": "meeting.completed"},
                                "steps": ["agent_run"], "params": {"instruction": "x"}}, headers=as_("ben-key"))
    r = client.post("/event-agents/pause", headers=as_("anna-key"))
    assert [p["name"] for p in r.json()["paused"]] == ["follow-up"]
    assert _active("user:126", "brief") == [1] and _active("user:512", "follow-up") == [1]
    assert client.post("/event-agents/pause", params={"everyone": True},
                       headers=as_("anna-key")).status_code == 403
    assert client.post("/event-agents/pause", params={"everyone": True}, headers=OPERATOR).json()["paused"]
    assert _active("user:512", "follow-up") == []

def test_a_routine_says_its_schedule_in_words_and_its_next_run(client):
    """The agent read a cron and guessed (founder, 2026-10-14). The listing says it."""
    client.post("/flows", json={**ROUTINE, "trigger": {"kind": "schedule", "cron": "*/30 6-21 * * *"}},
                headers=as_("anna-key"))
    mine = next(f for f in client.get("/flows", headers=as_("anna-key")).json()["flows"]
                if f["name"] == "brief" and f["scope"] == "user:126")
    assert mine["schedule"] == "every 30 min, 06:00–21:30 UTC"
    assert mine["next_run_at"] and mine["next_run_at"] > fa.clock.now()


def test_schedule_words():
    w = fa.schedule_words
    assert w("*/15 * * * *") == "every 15 min" and w("0 9 * * *") == "daily at 09:00 UTC"
    assert w("30 7 * * 1-5") == "at 07:30 UTC on weekdays" and w("5 * * * *") == "hourly at :05"


def test_every_sql_the_daily_count_runs_names_only_bound_parameters(monkeypatch):
    """GET /flows answered 500 on Postgres: a literal `:true` inside the SQL text is a bind parameter
    to SQLAlchemy (the SQLite double did not mind). Every bind name must be a parameter given."""
    from sqlalchemy import text
    from flows import admission
    seen = []

    class Spy:
        def execute(self, sql, params=None):
            seen.append((sql, dict(params or {})))
            return [(0,)]
    for scheduled in (None, True, False):
        admission.runs_today(Spy(), "126", 1000.0, scheduled=scheduled)
    for sql, params in seen:
        assert set(text(sql).compile().params) <= set(params), sql


def test_every_tool_the_manifest_publishes_is_served_with_its_arguments():
    """The MCP edge binds each flows tool to flows-api's own OpenAPI and refuses one whose route or
    argument is not there — `routine_claim` named a route the edge's stub lacked and the gate went
    red. This holds the REAL app to the manifest, route by route, argument by argument."""
    manifest = json.loads((Path(__file__).resolve().parents[1] / "mcp.tools.v1.json").read_text())
    spec = fa.app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for tool in manifest["tools"]:
        route = tool.get("route")
        if not route:
            continue
        op = spec["paths"].get(route["path"], {}).get(route["method"].lower())
        assert op is not None, f"{tool['name']}: flows-api does not serve {route['method']} {route['path']}"
        names = {p["name"] for p in op.get("parameters", [])}
        body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
        ref = body.get("$ref", "").rsplit("/", 1)[-1]
        names |= set((schemas.get(ref) or body).get("properties", {}))
        missing = [a for a in tool.get("arguments", []) if a not in names]
        assert not missing, f"{tool['name']}: arguments {missing} are not on {route['path']}"


# ── a person naming their OWN scope by address ───────────────────────────────────────────────────

def test_a_person_naming_their_own_scope_by_address_writes_it(client):
    """An agent that knows its person's address and not their uid sent `scope: user:<address>` and
    met `not_your_scope` for its own flow. The address is identity's own answer for this caller, so
    it is the same scope, filed under the uid like every other own-scope write."""
    r = client.post("/flows", json={**ROUTINE, "scope": "user:Anna@vexa.test"},
                    headers=as_("anna-key"))
    assert r.status_code == 201, r.text
    assert r.json()["scope"] == "user:126"
    r = client.post("/flows/brief/1/pause", params={"scope": "user:anna@vexa.test"},
                    headers=as_("anna-key"))
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "user:126"


def test_another_persons_address_is_still_not_your_scope(client):
    r = client.post("/flows", json={**ROUTINE, "scope": "user:ben@vexa.test"},
                    headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_scope"


def test_the_steps_field_says_it_takes_step_names_not_objects():
    """`steps` reached the agent as an array with no word on its items, and an agent filled it with
    `{"step": …, "params": …}` objects (422). The route's own schema is what the MCP edge
    republishes, so it carries the shape: names, and per-run settings in `params`."""
    steps = fa.app.openapi()["components"]["schemas"]["FlowSubmission"]["properties"]["steps"]
    assert steps["items"] == {"type": "string"}
    assert "params" in steps.get("description", "") and "agent_run" in steps["description"]
