"""A PERSON'S OWN CREDENTIAL, on the routes that are about that person — issue #1468.

Every route on this service used to take one deployment-wide operator key, and that key is not a
person: with it `GET /reactions` returns every reaction in the instance and
`POST /reactions/{id}/cancel` cancels any of them. So the MCP edge, which forwards the CALLER's own
credential and holds none of its own, could be wired two ways and both were wrong — refuse the
person (what it did: a 401 arriving inside a JSON-RPC envelope), or hand them the instance.

This is the third way. The subject-scoped routes accept the person's own Vexa credential, resolve
who that is through identity's `/internal/validate` — the one resolver, the same one the gateway
asks — and scope on the answer. The admin routes are untouched.

OFFLINE. `flows_api` builds its app at import and refuses to start without its credentials, so they
are supplied here before the import exactly as `test_health.py` does, with the SAME literals: the
module captures them as constants at import, and whichever test module imports first wins.

The identity hop is stubbed at its lowest seam — `subject_auth._validate`, the one HTTP call — so
the mapping from what identity answers to what this service answers is what is under test, and not
a mock of the answer we wanted.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

# UNREACHABLE Postgres DSN (port 1 is never a service) — `db_from_url` refuses anything that is
# not Postgres-shaped, and `postgres_db` is lazy, so importing `flows_api` against this address
# succeeds without a database running anywhere. The tests below DO touch the DB directly
# (`fa.db.execute(...)`), so `fa.db` is swapped for a real, working `SqliteDB` right after import
# — laziness proves the app composes with no database; the swap gives the tests one that works.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://subject-bearer:unreachable@127.0.0.1:1/flows"}
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

fa.db = SqliteDB()          # see the comment above _ENV: this file exercises the DB for real

OPERATOR = fa.API_KEY

#: Two people and one stranger's reaction. The two lineages `flows_timeline.model.concerns`
#: documents are both here on purpose: `meeting.completed` carries a uid and no address,
#: `invite.received` carries an organizer address and no uid.
ANNA = {"uid": "126", "email": "anna@vexa.test"}
BEN = {"uid": "512", "email": "ben@vexa.test"}

ROWS = {
    "r-anna-uid": {"uid": "126", "meeting_id": 104, "title": "Anna's standup"},
    "r-anna-email": {"organizer": "anna@vexa.test", "ics_uid": "a@b", "title": "Anna's invite"},
    "r-ben": {"uid": "512", "organizer": "ben@vexa.test", "meeting_id": 7, "title": "Ben's review"},
}
T0 = 1_788_000_000.0


@pytest.fixture(autouse=True)
def rows():
    """The reaction table, rebuilt for each test — two of Anna's, one of Ben's."""
    fa.db.execute("DELETE FROM reaction")
    for rid, refs in ROWS.items():
        fa.db.execute(
            """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                     flow, flow_version, step, status, attempt, next_run_at,
                                     reason, created_at, updated_at)
               VALUES (:rid,:sid,'meeting.completed',:refs,'post_meeting',1,'email_minutes',
                       'admitted',0,0,NULL,:c,:c)""",
            {"rid": rid, "sid": f"{rid}::post_meeting", "refs": json.dumps(refs), "c": T0})
    yield
    fa.db.execute("DELETE FROM reaction")


@pytest.fixture
def identity(monkeypatch):
    """Identity, stubbed at the ONE http hop `subject_auth` makes.

    `set(...)` installs a table of token -> what `/internal/validate` answers; `down(...)` makes the
    hop fail the way an unreachable service fails. Both are the real shapes: a 200 body with
    `user_id` and `email`, a 401 for a token nobody answers to, a 403 when OUR internal secret is
    wrong, and a transport error."""
    class Identity:
        def __init__(self):
            self.calls = []

        def set(self, table):
            def _validate(base, token, secret):
                self.calls.append((base, token, secret))
                return table.get(token, (401, {"detail": "Invalid token"}))
            monkeypatch.setattr(subject_auth, "_validate", _validate)

        def answers(self, code, body):
            def _validate(base, token, secret):
                self.calls.append((base, token, secret))
                return code, body
            monkeypatch.setattr(subject_auth, "_validate", _validate)

        def down(self):
            def _validate(base, token, secret):
                self.calls.append((base, token, secret))
                from flows import StepError
                raise StepError("http POST .../internal/validate: URLError: connection refused")
            monkeypatch.setattr(subject_auth, "_validate", _validate)

    ident = Identity()
    ident.set({
        "anna-key": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["legacy"], "credential_class": "session"}),
        "ben-key": (200, {"user_id": 512, "email": "ben@vexa.test", "scopes": ["legacy"], "credential_class": "session"}),
    })
    return ident


@pytest.fixture
def client():
    return TestClient(fa.app, raise_server_exceptions=False)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def admin():
    return {"X-Flows-Operator-Key": OPERATOR}


def admin_old_name():
    return {"X-Flows-Admin-Key": OPERATOR}


# ── A1 · a person's own credential lists their own reactions ─────────────────────────────────────

def test_a_persons_own_bearer_lists_their_own_reactions(client, identity):
    r = client.get("/reactions", headers=bearer("anna-key"))
    assert r.status_code == 200, r.text
    assert {x["id"] for x in r.json()["reactions"]} == {"r-anna-uid", "r-anna-email"}


def test_the_operator_key_reads_one_named_subject(client, identity):
    """The operator read — NOW SCOPED, always (P20/E1). It used to answer with the instance when no
    subject was named, and the route function also shadowed the model function it calls, so every
    authenticated call raised `TypeError: list_reactions() got multiple values for argument
    'status'` and answered 500. The 401 in front of it is what hid that for as long as it did.

    Ben's row is the assertion that matters: a named subject is a scope, not a label. Anna's INVITE
    row is absent here and that is honest — the operator key carries no (uid, address) pair of its
    own, so the model asks admin-api for the other half, admin-api is `127.0.0.1:1` in this suite,
    and the address lineage cannot be matched. A person's own credential carries both halves and
    sees both rows (the test above)."""
    r = client.get("/reactions", params={"subject": "126"}, headers=admin())
    assert r.status_code == 200, r.text
    assert {x["id"] for x in r.json()["reactions"]} == {"r-anna-uid"}
    assert "r-ben" not in r.text


def test_the_operator_key_with_no_subject_reads_nothing(client, identity):
    """THE P20 HOLE, closed. `Caller(kind="admin")` carries no uid, no email and no owner, so with
    no subject there was nothing left to authorize against and this route answered with every
    reaction the deployment held — flow names, step names, failure reasons and reaction ids, for
    every tenant. `VEXA_FLOWS_API_KEY` is exported into five compose services."""
    r = client.get("/reactions", headers=admin())
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["subject_required"] is True
    assert "r-ben" not in r.text


def test_the_api_key_header_carries_a_bearer_too(client, identity):
    """The MCP edge forwards the caller's credential as `X-API-Key` (register.py) and the gateway
    resolves the same header, so both spellings of ONE credential reach this door."""
    r = client.get("/reactions", headers={"X-API-Key": "anna-key"})
    assert r.status_code == 200 and {x["id"] for x in r.json()["reactions"]} == {
        "r-anna-uid", "r-anna-email"}


# ── A2 · and nobody else's ───────────────────────────────────────────────────────────────────────

def test_another_persons_bearer_sees_none_of_the_first_persons_rows(client, identity):
    r = client.get("/reactions", headers=bearer("ben-key"))
    assert r.status_code == 200
    assert {x["id"] for x in r.json()["reactions"]} == {"r-ben"}


def test_cancelling_a_reaction_that_is_not_yours_is_refused(client, identity):
    r = client.post("/reactions/r-ben/cancel", headers=bearer("anna-key"), json={})
    assert r.status_code == 403, r.text
    assert fa.db.execute("SELECT status FROM reaction WHERE reaction_id='r-ben'")[0][0] == "admitted"


def test_cancelling_your_own_reaction_works(client, identity):
    r = client.post("/reactions/r-anna-uid/cancel", headers=bearer("anna-key"), json={})
    assert r.status_code == 200, r.text
    assert fa.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-anna-uid'")[0][0] == "cancelled"


def test_ownership_is_checked_even_when_no_subject_argument_is_sent(client, identity):
    """Before this, ownership was checked only when the CALLER passed `subject=` — an argument the
    caller asserts. A person who simply omitted it got the unscoped operator behaviour."""
    r = client.post("/reactions/r-ben/retry", headers=bearer("anna-key"), json={})
    assert r.status_code == 403


# ── A3 · no credential opens nothing ─────────────────────────────────────────────────────────────

SUBJECT_SCOPED = [("GET", "/flows"), ("GET", "/reactions"),
                  ("POST", "/reactions/r-anna-uid/cancel"), ("GET", "/timeline?subject=126")]


@pytest.mark.parametrize("method,path", SUBJECT_SCOPED)
def test_no_credential_at_all_is_refused(client, identity, method, path):
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path", SUBJECT_SCOPED)
def test_the_operator_key_still_opens_every_one_of_them(client, identity, method, path):
    r = client.request(method, path, headers=admin(), json={} if method == "POST" else None)
    assert r.status_code != 401, f"{method} {path} -> {r.status_code}: {r.text[:200]}"


# ── A4 · the admin routes are still admin-keyed ──────────────────────────────────────────────────

#: `POST /flows` and the lifecycle verb left this list with ADR-0046: a person now authors and
#: retires flows of their OWN (tests/test_user_flows.py holds what they may not touch).
ADMIN_ONLY = [
    ("POST", "/events", {"event_type": "meeting.completed", "source_event_id": "s1", "refs": {}}),
    ("POST", "/events/batch", {"meetings": [{"url": "https://m.test/a", "organizer": "a@b.test",
                                             "start": T0}]}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_a_persons_bearer_does_not_open_an_admin_route(client, identity, method, path, body):
    r = client.request(method, path, headers=bearer("anna-key"), json=body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_the_operator_key_still_opens_the_admin_routes(client, identity, method, path, body):
    """`!= 401` rather than a status: past the door each of these has its own answer — the instance
    gate's 409, an unknown flow version's 404 — and this row is about the door."""
    r = client.request(method, path, headers=admin(), json=body)
    assert r.status_code != 401, f"{method} {path} -> {r.status_code}: {r.text[:200]}"


# ── A5 · the subject is derived, and a subject that is not yours is refused ──────────────────────

def test_a_subject_argument_naming_someone_else_is_refused(client, identity):
    r = client.get("/reactions", params={"subject": "512"}, headers=bearer("anna-key"))
    assert r.status_code == 403, r.text
    assert "512" in json.dumps(r.json())


def test_naming_yourself_by_uid_is_accepted(client, identity):
    r = client.get("/reactions", params={"subject": "126"}, headers=bearer("anna-key"))
    assert r.status_code == 200
    assert {x["id"] for x in r.json()["reactions"]} == {"r-anna-uid", "r-anna-email"}


def test_naming_yourself_by_email_is_accepted(client, identity):
    r = client.get("/reactions", params={"subject": "ANNA@vexa.test"}, headers=bearer("anna-key"))
    assert r.status_code == 200
    assert {x["id"] for x in r.json()["reactions"]} == {"r-anna-uid", "r-anna-email"}


def test_the_answer_states_which_subject_it_is_about(client, identity):
    """An argument silently overridden is the same defect as one silently dropped: the caller has
    to be able to see whose rows came back."""
    assert client.get("/reactions", headers=bearer("anna-key")).json()["subject"] == "126"
    assert client.get("/reactions", params={"subject": "512"},
                      headers=admin()).json()["subject"] == "512"


def test_an_operator_may_still_read_any_subject(client, identity):
    r = client.get("/reactions", params={"subject": "512"}, headers=admin())
    assert r.status_code == 200
    assert {x["id"] for x in r.json()["reactions"]} == {"r-ben"}


# ── A6 · an unreachable resolver is not a verdict on the credential ──────────────────────────────

def test_identity_unreachable_is_503_not_401(client, identity):
    identity.down()
    r = client.get("/reactions", headers=bearer("anna-key"))
    assert r.status_code == 503, r.text


def test_identity_saying_the_token_is_unknown_is_401(client, identity):
    r = client.get("/reactions", headers=bearer("no-such-key"))
    assert r.status_code == 401, r.text


def test_our_own_internal_secret_being_wrong_is_not_the_callers_fault(client, identity):
    """identity answers 403 when the INTERNAL secret is wrong. That is a deployment fault; telling
    the person their credential is invalid would send them to rotate a working key."""
    identity.answers(403, {"detail": "Invalid internal secret"})
    r = client.get("/reactions", headers=bearer("anna-key"))
    assert r.status_code == 503, r.text


def test_the_resolver_is_the_one_the_gateway_asks(client, identity):
    """P23 — one resolver of who the caller is. Not a second lookup of our own: the same
    `/internal/validate`, over the internal secret, that `gateway/adapters.py` posts to."""
    client.get("/reactions", headers=bearer("anna-key"))
    base, token, secret = identity.calls[-1]
    assert token == "anna-key" and secret == "test-internal-secret"


# ── the other two subject-scoped routes ──────────────────────────────────────────────────────────

def test_the_timeline_takes_a_persons_bearer_and_answers_about_them(client, identity):
    r = client.get("/timeline", params={"meetings": "false"}, headers=bearer("anna-key"))
    assert r.status_code == 200, r.text
    assert r.json()["uid"] == "126"


def test_the_timeline_refuses_a_subject_that_is_not_yours(client, identity):
    r = client.get("/timeline", params={"subject": "512", "meetings": "false"},
                   headers=bearer("anna-key"))
    assert r.status_code == 403, r.text


def test_the_flow_listing_takes_a_persons_bearer(client, identity):
    r = client.get("/flows", headers=bearer("anna-key"))
    assert r.status_code == 200 and "steps_vocabulary" in r.json()


# ── the operator header says what it holds ──────────────────────────────────────────────────────

def test_the_operator_key_travels_under_a_name_that_says_what_it_is(client, identity):
    """`X-Flows-Admin-Key` reads as admin-api's token and is not: it is flows-api's own operator
    key. The two have been confused on a live deployment, which is how a lane came to run on the
    string `changeme`. Splitting this surface into two tiers is the moment the name has to stop
    lying."""
    assert client.get("/reactions", params={"subject": "126"},
                      headers=admin()).status_code == 200


def test_the_old_name_still_opens_the_door_for_one_release(client, identity):
    assert client.get("/reactions", params={"subject": "126"},
                      headers=admin_old_name()).status_code == 200
    assert client.post("/events", headers=admin_old_name(), json={
        "event_type": "meeting.completed", "source_event_id": "s-old", "refs": {}}
    ).status_code != 401


def _prose(out: str) -> str:
    """Stdout minus the logevent.v1 lines every request now emits (one JSON object per line, with a
    `trace_id`): what these tests read is the human-facing deprecation notice, not the trace."""
    import json as _j
    kept = []
    for line in out.splitlines(keepends=True):
        try:
            if "trace_id" in _j.loads(line):
                continue
        except ValueError:
            pass
        kept.append(line)
    return "".join(kept)


def test_the_old_name_says_once_that_it_is_deprecated(client, identity, capsys):
    fa._DEPRECATED_HEADER_SAID.clear()
    client.get("/reactions", headers=admin_old_name())
    first = _prose(capsys.readouterr().out)
    assert "X-Flows-Admin-Key" in first and "X-Flows-Operator-Key" in first
    client.get("/reactions", headers=admin_old_name())
    assert _prose(capsys.readouterr().out) == "", "once per process, not once per request"


def test_the_new_name_wins_when_both_are_sent(client, identity):
    r = client.get("/reactions", params={"subject": "126"},
                   headers={"X-Flows-Operator-Key": OPERATOR,
                            "X-Flows-Admin-Key": "not-the-key"})
    assert r.status_code == 200


def test_a_wrong_operator_key_under_either_name_is_refused(client, identity):
    assert client.get("/reactions", headers={"X-Flows-Operator-Key": "wrong"}).status_code == 401
    assert client.get("/reactions", headers={"X-Flows-Admin-Key": "wrong"}).status_code == 401


# ── `GET /queue/waiting` — the fifth subject-scoped route (#1482 landed it operator-keyed) ──────

@pytest.fixture
def queue_subject(monkeypatch):
    """Records WHICH subject the queue projection was asked about. The door is what is under test
    here; `tests/test_queue_waiting.py` owns what the projection answers."""
    asked = []

    def waiting(_db, *, subject, flows, limit, identity=None, room=None):
        # `identity=` is B1: the route hands the projection the (uid, email) pair it already
        # resolved from the caller's own credential, rather than making it re-ask admin-api for a
        # fact `/internal/validate` returned on the way in.
        asked.append((subject, identity))
        return {"items": [], "flows": []}

    monkeypatch.setattr(fa._flows_queue, "waiting", waiting)
    return asked


def test_the_queue_takes_a_persons_bearer_and_answers_about_them(client, identity, queue_subject):
    r = client.get("/queue/waiting", headers=bearer("anna-key"))
    assert r.status_code == 200, r.text
    (subject, identity_fn), = queue_subject
    assert subject == "126"
    assert identity_fn is not None and identity_fn("126") == ("126", "anna@vexa.test"), (
        "B1: the queue was left to re-resolve a pair the credential already carried")


def test_the_queue_refuses_a_subject_that_is_not_yours(client, identity, queue_subject):
    r = client.get("/queue/waiting", params={"subject": "512"}, headers=bearer("anna-key"))
    assert r.status_code == 403, r.text
    assert queue_subject == [], "nothing was read"


def test_a_stamped_user_id_does_not_beat_an_authenticated_person(client, identity, queue_subject):
    """`X-User-Id` is a header a caller can type. It is trustworthy on this route only because the
    OPERATOR key gates it — a service identity vouching for a person it resolved. A person's own
    credential is stronger evidence than any header, so when one is present it wins.

    This route is not always behind the gateway: reached through the MCP edge it is addressed
    directly, and the edge stamps no `X-User-Id` at all."""
    r = client.get("/queue/waiting", headers={**bearer("anna-key"), "X-User-Id": "512"})
    assert r.status_code == 200
    assert [s for s, _ in queue_subject] == ["126"], "a header overrode a verified credential"


def test_the_operator_read_still_honours_the_stamped_identity(client, identity, queue_subject):
    """Unchanged: with the operator key, `X-User-Id` is the gateway's answer and still wins over
    `?subject=` — `tests/test_queue_waiting.py` owns that row and it must keep passing."""
    r = client.get("/queue/waiting", params={"subject": "999"},
                   headers={**admin(), "X-User-Id": "126"})
    assert r.status_code == 200 and [s for s, _ in queue_subject] == ["126"]


def test_the_queue_needs_a_credential(client, identity, queue_subject):
    assert client.get("/queue/waiting", params={"subject": "126"}).status_code == 401
    assert queue_subject == []


# ── a worker acting for a person (a delegation token) ────────────────────────────────────────────
#
# Identity answers a worker's `vxd_` token with the person it acts for plus `delegation`, the
# dispatch's ceiling — and only to a resolver that declares it reads one. Flows declares it, carries
# the delegation on `Caller`, and refuses a worker with nobody in the loop every route
# `DELEGATED_REACH` classifies PERSON_ONLY. The cases below are GENERATED from that one table.

def _worker_answer(regime, *, uid=126, email="anna@vexa.test"):
    """What `/internal/validate` answers a declared caller for a worker acting for Anna."""
    body = {"user_id": uid, "email": email, "scopes": ["bot", "tx"], "is_admin": False,
            "person_is_admin": False}
    if regime is not ...:
        body["delegation"] = {"workspaces": "*"} if regime is None else {"regime": regime,
                                                                         "workspaces": "*"}
    return 200, body


#: Every regime that leaves nobody in the loop: the sealed unwatched one, an empty one, one identity
#: does not seal, a missing `regime`, and an answer that lost `delegation` altogether (`...`).
UNWATCHED = ["autonomous", "", "root", None, ...]


@pytest.fixture
def workers(identity):
    """Anna's own key, plus one worker token acting for Anna per regime."""
    table = {"anna-key": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["legacy"], "credential_class": "session"}),
             "vxd_human": _worker_answer("human"),
             "vxd_human_upper": _worker_answer(" Human ")}
    for i, regime in enumerate(UNWATCHED):
        table[f"vxd_unwatched_{i}"] = _worker_answer(regime)
    identity.set(table)
    return identity


UNWATCHED_TOKENS = [f"vxd_unwatched_{i}" for i in range(len(UNWATCHED))]
PERSON_ONLY_ROUTES = sorted(k for k, v in fa.DELEGATED_REACH.items() if v == fa.PERSON_ONLY)
OPEN_ROUTES = sorted(k for k, v in fa.DELEGATED_REACH.items() if v == fa.OPEN)
#: A concrete URL for each templated path: Anna's own reaction, the verb that calls off her join.
PATH_PARAMS = {"reaction_id": "r-anna-uid", "verb": "cancel",
               "name": "annas-brief", "version": 1, "action": "pause", "kind": "minutes"}
#: The body each POST needs to get past validation — a person's own routine for `POST /flows`.
BODIES = {"/flows": {"name": "annas-brief", "trigger": {"kind": "schedule", "cron": "0 7 * * *"},
                     "steps": ["agent_run"], "params": {"instruction": "brief me"}},
          "/flows/{name}/claim": {"thread": "pchat-anna"},
          "/concerns/stage": {"thread": "pchat-anna", "stage": "waiting"},
          "/sinks": {"thread": "pchat-anna", "name": "annas-brief", "event": "mail.received"},
          "/sensors": {"name": "annas-new-sensor", "kind": "calendar"},
          "/sensors/{name}/connect": {"thread": "pchat-anna"},
          "/routines/triggers": {"thread": "pchat-anna", "name": "annas-check",
                                 "trigger": {"kind": "schedule", "cron": "0 8 * * *"},
                                 "instruction": "check"}}


def _url(path):
    return path.format(**PATH_PARAMS)


def _call(client, method, path, token):
    fa.db.execute("DELETE FROM flow_version WHERE scope = 'user:126'")
    fa.db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
                  "status, created_by, created_at) VALUES ('user:126','annas-brief',1,"
                  "'schedule.due','0 7 * * *','[\"agent_run\"]','{\"instruction\":\"x\"}',"
                  "'active','t',1.0)")
    # …and her own sensor of the same name (ADR-0050), for the sensor verbs
    fa.db.execute("DELETE FROM sensor WHERE scope = 'user:126'")
    fa.db.execute("INSERT INTO sensor (scope, name, kind, interval_s, version, status, mode, "
                  "next_due_at, failures, created_at) VALUES ('user:126','annas-brief','calendar',"
                  "60,1,'draft','poll',0,0,1.0)")
    return client.request(method, _url(path), headers=bearer(token),
                          json=BODIES.get(path, {}) if method == "POST" else None)


def _subject_routes():
    """(METHOD, path) of every route a person's credential reaches — the ones whose dependencies
    include `subject_or_operator` or `timeline_reader`."""
    from fastapi.routing import APIRoute

    def deps(dependant):
        for d in dependant.dependencies:
            yield d.call
            yield from deps(d)

    doors = {fa.subject_or_operator, fa.timeline_reader}
    return {(m, r.path) for r in fa.app.routes if isinstance(r, APIRoute)
            for m in r.methods if doors & set(deps(r.dependant))}


def test_every_route_a_person_reaches_is_classified_for_a_worker():
    """ONE table says what a worker with nobody in the loop may do. A new subject-scoped route that
    is not in it is refused to such a worker (fail closed) and fails here until it is classified."""
    assert _subject_routes() == set(fa.DELEGATED_REACH)
    assert set(fa.DELEGATED_REACH.values()) <= {fa.PERSON_ONLY, fa.OPEN}
    assert PERSON_ONLY_ROUTES, "steering a reaction acts as the person"


def test_flows_declares_to_identity_that_it_reads_a_delegation(monkeypatch):
    """Identity answers a worker's token only to a resolver that declares it reads `delegation`
    (identity.v1 AcceptsDelegationHeader). Flows sends it beside its internal secret."""
    import flows_steps.common as common
    seen = []
    monkeypatch.setattr(common, "http", lambda method, url, headers, body=None, timeout=20:
                        seen.append((method, url, headers, body)) or (200, {}))
    subject_auth._validate("http://admin-api:8001", "vxd_tok", "the-secret")
    (method, url, headers, body), = seen
    assert (method, url, body) == ("POST", "http://admin-api:8001/internal/validate",
                                   {"token": "vxd_tok"})
    assert headers[subject_auth.ACCEPTS_DELEGATION_HEADER] == "1"
    assert headers["X-Internal-Secret"] == "the-secret"


def test_the_declaration_is_identitys_sealed_header():
    root = next(p for p in Path(__file__).resolve().parents if (p / "core" / "identity").is_dir())
    schema = json.loads(
        (root / "core/identity/contracts/identity.v1/identity.schema.json").read_text())
    assert (subject_auth.ACCEPTS_DELEGATION_HEADER
            == schema["$defs"]["AcceptsDelegationHeader"]["const"])


def test_a_workers_caller_carries_its_delegation(monkeypatch, workers):
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8001")
    caller = subject_auth.resolve("vxd_human", secret="s")
    assert (caller.kind, caller.uid, caller.email) == ("subject", "126", "anna@vexa.test")
    assert caller.delegation == {"regime": "human", "workspaces": "*"}
    assert caller.is_delegated and caller.regime == "human" and not caller.is_unwatched
    person = subject_auth.resolve("anna-key", secret="s")
    assert person.delegation is None and not person.is_delegated and not person.is_unwatched


@pytest.mark.parametrize("token", UNWATCHED_TOKENS)
def test_a_worker_with_nobody_in_the_loop_is_unwatched(monkeypatch, workers, token):
    """`identity_token.is_unwatched`'s rule: delegated, and a regime other than `human` — an empty,
    unknown or missing one included, and a `vxd_` answer that lost its delegation too."""
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8001")
    caller = subject_auth.resolve(token, secret="s")
    assert caller.is_delegated and caller.is_unwatched and caller.uid == "126"


@pytest.mark.parametrize("token", UNWATCHED_TOKENS)
@pytest.mark.parametrize("method,path", PERSON_ONLY_ROUTES)
def test_a_worker_with_nobody_in_the_loop_is_refused_every_person_only_verb(
        client, workers, method, path, token):
    r = _call(client, method, path, token)
    assert r.status_code == 403, f"{method} {path} as {token} -> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == subject_auth.REFUSAL
    assert fa.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-anna-uid'")[0][0] == "admitted"


#: The mail-design writes are PERSON_ONLY for a worker AND the admin account's alone — a second gate
#: an everyday person does not pass (`test_mail_admin.py::test_refusals`), so they are not in the
#: "an everyday person is admitted" set below.
ADMIN_ACCOUNT_ROUTES = {r for r in PERSON_ONLY_ROUTES if r[1].startswith("/mail/")}


@pytest.mark.parametrize("token", ["vxd_human", "vxd_human_upper", "anna-key"])
@pytest.mark.parametrize("method,path", [r for r in PERSON_ONLY_ROUTES if r not in ADMIN_ACCOUNT_ROUTES])
def test_a_worker_in_the_loop_and_the_person_are_admitted_to_person_only_verbs(
        client, workers, method, path, token, monkeypatch):
    from flows_steps import common, routine
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")   # past the door, agent-api answers
    monkeypatch.setattr(routine, "http", lambda m, u, h, b=None, timeout=20: (200, {"ok": True}))
    r = _call(client, method, path, token)
    assert r.status_code in (200, 201), (
        f"{method} {path} as {token} -> {r.status_code}: {r.text[:200]}")


@pytest.mark.parametrize("token", UNWATCHED_TOKENS)
@pytest.mark.parametrize("method,path", OPEN_ROUTES)
def test_a_worker_with_nobody_in_the_loop_still_reads_and_reports(
        client, workers, monkeypatch, method, path, token):
    """The OPEN routes are not refused to it — whatever each answers past the door is its own."""
    monkeypatch.setattr(fa, "fetch_meetings", lambda uid: [])
    r = _call(client, method, path, token)
    assert r.status_code not in (401, 503), f"{method} {path} -> {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
        "application/json") and isinstance(r.json(), dict) else None
    assert detail != subject_auth.REFUSAL, f"{method} {path} refused an unwatched worker"


def test_the_refusal_is_the_one_every_service_answers_with():
    """gateway-identity.v1's `REFUSAL`, word for word (gate:fact-parity holds the copy)."""
    assert subject_auth.REFUSAL["status"] == "refused"
    assert subject_auth.REFUSAL["reason"] == "human_session_required"
    assert subject_auth.REFUSAL["remedy"] == "ask_in_chat"
    assert "ask in chat" in subject_auth.REFUSAL["tell_your_person"]


# ── a worker whose turn other people read (delegation.shared, ADR-0048 decision 4) ─────────────
#
# A person's flows, reactions, timeline and queue are theirs alone; a shared conversation's answer
# may use only what every participant can read. So a shared worker is refused every route here but
# filing a friction report into Vexa's own sink, with the body every service answers it with.

SHARED_OPEN = {("POST", "/friction")}
#: …and the one route that ANSWERS a shared turn with less instead of refusing it (friction 7,
#: 2026-10-16): `whats_waiting`, narrowed to what every reader of the room can see.
SHARED_NARROWED = {("GET", "/queue/waiting")}


@pytest.fixture
def shared_workers(identity):
    table = {"anna-key": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["legacy"], "credential_class": "session"}),
             "vxd_shared": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["bot"],
                                  "is_admin": False, "person_is_admin": False,
                                  "delegation": {"regime": "human", "workspaces": ["team"],
                                                 "shared": True}}),
             "vxd_private": _worker_answer("human"),
             # D2: a room's turn as the WORKSPACE principal; refused even if `shared` went missing
             "vxd_room": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["bot", "tx"],
                                "is_admin": False, "person_is_admin": False,
                                "delegation": {"regime": "human", "workspaces": ["team"],
                                               "principal_kind": "workspace", "principal_workspace": "team",
                                               "on_behalf_of": "127", "bills_to": 126}})}
    identity.set(table)
    return identity


def test_a_shared_workers_caller_says_so(monkeypatch, shared_workers):
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8001")
    assert subject_auth.resolve("vxd_shared", secret="s").is_shared
    assert subject_auth.resolve("vxd_room", secret="s").is_shared, "a workspace principal is shared (D2)"
    assert not subject_auth.resolve("vxd_private", secret="s").is_shared
    assert not subject_auth.resolve("anna-key", secret="s").is_shared


@pytest.mark.parametrize("method,path", sorted(set(fa.DELEGATED_REACH) - SHARED_OPEN - SHARED_NARROWED))
@pytest.mark.parametrize("token", ["vxd_shared", "vxd_room"])
def test_a_shared_turn_is_refused_every_flows_route(client, shared_workers, monkeypatch, method, path,
                                                     token):
    monkeypatch.setattr(fa, "fetch_meetings", lambda uid: [])
    r = _call(client, method, path, token)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}: {r.text[:200]}"
    assert r.json()["detail"] == subject_auth.SHARED_REFUSAL
    assert fa.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-anna-uid'")[0][0] == "admitted"


@pytest.mark.parametrize("method,path", sorted(set(fa.DELEGATED_REACH) - SHARED_OPEN))
def test_a_private_turn_is_not_refused_by_the_shared_rule(client, shared_workers, monkeypatch,
                                                          method, path):
    monkeypatch.setattr(fa, "fetch_meetings", lambda uid: [])
    r = _call(client, method, path, "vxd_private")
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
        "application/json") and isinstance(r.json(), dict) else None
    assert detail != subject_auth.SHARED_REFUSAL


#: Anna's queue as the room sees it: the two private rows above (her standup, her invite) plus one
#: item scoped to the room's workspace `team`, one to a workspace the room cannot read, and one to
#: her own home (the workspace named by her uid) — which is private however it is spelled.
ROOM_ROWS = {
    "r-anna-team": {"uid": "126", "workspace": "team", "title": "Team retro"},
    "r-anna-elsewhere": {"uid": "126", "workspace": "board", "title": "Board prep"},
    "r-anna-home": {"uid": "126", "workspace": "126", "title": "Anna's own notes"},
    "r-anna-mail": {"organizer": "anna@vexa.test", "workspace": "", "title": "Anna's private mail"},
}


def _seed_room_rows():
    for rid, refs in ROOM_ROWS.items():
        fa.db.execute(
            """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                     flow, flow_version, step, status, attempt, next_run_at,
                                     reason, created_at, updated_at)
               VALUES (:rid,:sid,'meeting.completed',:refs,'post_meeting',1,'email_minutes',
                       'admitted',0,0,NULL,:c,:c)""",
            {"rid": rid, "sid": f"{rid}::post_meeting", "refs": json.dumps(refs), "c": T0})


def test_a_shared_turn_gets_the_room_scoped_queue_not_a_refusal(client, shared_workers):
    """Friction 7 (live 12:42): `whats_waiting` in a room answers with what every reader of the
    room can see — items scoped to a workspace on the shared ceiling — instead of 403."""
    _seed_room_rows()
    r = client.get("/queue/waiting", headers=bearer("vxd_shared"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "room"
    assert {x["id"] for x in body["items"]} == {"r-anna-team"}
    assert body["waiting"] == 1


def test_the_room_scoped_queue_never_carries_the_persons_private_items(client, shared_workers):
    """THE LEAK RULE (ADR-0048 decision 4): nothing of the person's private queue — her meetings,
    invites, mail, home-workspace items, other workspaces — and not even how many were withheld."""
    _seed_room_rows()
    body = client.get("/queue/waiting", headers=bearer("vxd_shared")).json()
    text = json.dumps(body)
    for private in ("r-anna-uid", "r-anna-email", "r-anna-elsewhere", "r-anna-home", "r-anna-mail",
                    "standup", "Anna's invite", "Board prep", "own notes", "private mail"):
        assert private not in text, private
    assert body["quiet"] == 0 and "withheld" not in body
    # …and the same person in her private chat still sees her whole queue
    whole = client.get("/queue/waiting", headers=bearer("anna-key")).json()
    assert {"r-anna-uid", "r-anna-email", "r-anna-team", "r-anna-home"} <= {x["id"] for x in whole["items"]}
    assert "scope" not in whole


def test_a_shared_turn_with_no_readable_workspace_sees_an_empty_queue(client, identity):
    """FAILS CLOSED: a shared ceiling that names no workspace (or `*`, never a shared answer) gets
    nothing, not the person's queue."""
    for ws in ([], "*", None):
        identity.set({"vxd_shared": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["bot"],
                                           "delegation": {"regime": "human", "workspaces": ws,
                                                          "shared": True}})})
        _seed_room_rows() if not fa.db.execute(
            "SELECT 1 FROM reaction WHERE reaction_id='r-anna-team'") else None
        r = client.get("/queue/waiting", headers=bearer("vxd_shared"))
        assert r.status_code == 200, r.text
        assert r.json()["items"] == [] and r.json()["scope"] == "room"


def test_the_shared_refusal_is_the_one_every_service_answers_with():
    assert subject_auth.SHARED_REFUSAL["status"] == "refused"
    assert subject_auth.SHARED_REFUSAL["reason"] == "shared_conversation"
