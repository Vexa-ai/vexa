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
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://subject-bearer:unreachable@127.0.0.1:1/flows"}
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
        "anna-key": (200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["legacy"]}),
        "ben-key": (200, {"user_id": 512, "email": "ben@vexa.test", "scopes": ["legacy"]}),
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


def test_the_operator_key_still_lists_every_reaction(client, identity):
    """The unscoped operator read this route has always had — and the reason A1 could never have
    been observed before: the route function shadowed the model function it calls, so every
    authenticated call raised `TypeError: list_reactions() got multiple values for argument
    'status'` and answered 500. The 401 in front of it is what hid that for as long as it did."""
    r = client.get("/reactions", headers=admin())
    assert r.status_code == 200, r.text
    assert {x["id"] for x in r.json()["reactions"]} == set(ROWS)


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

ADMIN_ONLY = [
    ("POST", "/flows", {"name": "x", "on_event": "e", "steps": ["email_minutes"]}),
    ("POST", "/events", {"event_type": "meeting.completed", "source_event_id": "s1", "refs": {}}),
    ("POST", "/events/batch", {"meetings": [{"url": "https://m.test/a", "organizer": "a@b.test",
                                             "start": T0}]}),
    ("POST", "/flows/post_meeting/1/retire", {}),
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
    assert client.get("/reactions", headers=admin()).json()["subject"] == ""


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
    assert client.get("/reactions", headers=admin()).status_code == 200


def test_the_old_name_still_opens_the_door_for_one_release(client, identity):
    assert client.get("/reactions", headers=admin_old_name()).status_code == 200
    assert client.post("/events", headers=admin_old_name(), json={
        "event_type": "meeting.completed", "source_event_id": "s-old", "refs": {}}
    ).status_code != 401


def test_the_old_name_says_once_that_it_is_deprecated(client, identity, capsys):
    fa._DEPRECATED_HEADER_SAID.clear()
    client.get("/reactions", headers=admin_old_name())
    first = capsys.readouterr().out
    assert "X-Flows-Admin-Key" in first and "X-Flows-Operator-Key" in first
    client.get("/reactions", headers=admin_old_name())
    assert capsys.readouterr().out == "", "once per process, not once per request"


def test_the_new_name_wins_when_both_are_sent(client, identity):
    r = client.get("/reactions", headers={"X-Flows-Operator-Key": OPERATOR,
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

    def waiting(_db, *, subject, flows, limit):
        asked.append(subject)
        return {"items": [], "flows": []}

    monkeypatch.setattr(fa._flows_queue, "waiting", waiting)
    return asked


def test_the_queue_takes_a_persons_bearer_and_answers_about_them(client, identity, queue_subject):
    r = client.get("/queue/waiting", headers=bearer("anna-key"))
    assert r.status_code == 200, r.text
    assert queue_subject == ["126"]


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
    assert queue_subject == ["126"], "a header overrode a verified credential"


def test_the_operator_read_still_honours_the_stamped_identity(client, identity, queue_subject):
    """Unchanged: with the operator key, `X-User-Id` is the gateway's answer and still wins over
    `?subject=` — `tests/test_queue_waiting.py` owns that row and it must keep passing."""
    r = client.get("/queue/waiting", params={"subject": "999"},
                   headers={**admin(), "X-User-Id": "126"})
    assert r.status_code == 200 and queue_subject == ["126"]


def test_the_queue_needs_a_credential(client, identity, queue_subject):
    assert client.get("/queue/waiting", params={"subject": "126"}).status_code == 401
    assert queue_subject == []
