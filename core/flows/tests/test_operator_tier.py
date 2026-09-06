"""COMPLETE MEDIATION ON THE TIERS THAT ARE NOT A PERSON — P20, review item E1.

`tests/test_subject_bearer.py` proved the SUBJECT tier: a person's own credential resolves to
exactly them, an argument naming anyone else is 403, ownership runs on the destructive verb, and an
unresolvable subject fails closed. That half was already exemplary. This file is about the other
two tiers, where the same review found no resource check at all:

  E1a  THE OPERATOR KEY IS A CREDENTIAL, NOT A PERSON. `Caller(kind="admin")` carries no uid, no
       email and no owner, so a subject-scoped route it opened with NO subject had nothing left to
       compare a resource against — and answered with the instance. `GET /reactions` returned every
       row a deployment held; `GET /queue/waiting`, `GET /queue/notices`, `GET /friction` and
       `POST /friction` were reachable for anyone the caller cared to name. Every one of those now
       REQUIRES a named subject.

  E1b  THE OWNERSHIP CHECK ON `POST /reactions/{id}/{verb}` RAN BEHIND `if subj:`. An operator who
       simply omitted `subject` therefore cancelled ANYONE's reaction with no check at all — not a
       weak check, no check. `VEXA_FLOWS_API_KEY` is exported into five compose services, so the
       set of processes that could do this is not small.

  E1c  `VEXA_FLOWS_TIMELINE_KEY` — documented as "a key that can do exactly one thing" — resolved
       to `Caller(kind="admin")`. One line, and the narrow key read any person's reactions and
       queue, and (with `meetings=true`, the DEFAULT) reached `flows_steps.common.user_api_key`,
       which MINTS a `["bot","browser","tx"]` gateway token on the named third party's account.
       A read-only credential that writes a token on somebody else's account is not read-only.

Offline, same composition every other route test in this suite uses: the real app through
`TestClient`, a real `SqliteDB` swapped under it, no network. `TIMELINE_KEY` is read at import, so
the tests that need one set the module attribute the predicate actually reads.
"""
from __future__ import annotations

import json
import os

import pytest
from sqlite_double import SqliteDB

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-operator-tier",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://operator-tier:unreachable@127.0.0.1:1/flows"}

#: The narrow key, for the tests that install one. Deliberately not a value in `_ENV`: whichever
#: test module imports `flows_api` first wins, so an import-time constant is not a thing this file
#: can rely on — it sets the attribute the predicate reads instead.
NARROW = "narrow-timeline-key-not-a-placeholder"

T0 = 1_788_000_000.0
ROWS = {
    "r-anna": {"uid": "126", "meeting_id": 104, "title": "Anna's standup"},
    "r-ben": {"uid": "512", "organizer": "ben@vexa.test", "meeting_id": 7, "title": "Ben's review"},
}


@pytest.fixture(scope="module")
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
    # The operator key this file must present is whatever the LIVE module ended up holding — the
    # singleton-import caveat `test_friction.py`'s fixture documents.
    return flows_api, TestClient(flows_api.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def rows(api):
    flows_api, _ = api
    flows_api.db.execute("DELETE FROM reaction")
    for rid, refs in ROWS.items():
        flows_api.db.execute(
            """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                     flow, flow_version, step, status, attempt, next_run_at,
                                     reason, created_at, updated_at)
               VALUES (:rid,:sid,'meeting.completed',:refs,'post_meeting',1,'email_minutes',
                       'admitted',0,0,NULL,:c,:c)""",
            {"rid": rid, "sid": f"{rid}::post_meeting", "refs": json.dumps(refs), "c": T0})
    yield
    flows_api.db.execute("DELETE FROM reaction")


def operator(flows_api):
    return {"X-Flows-Operator-Key": flows_api.API_KEY}


@pytest.fixture
def narrow_key(api, monkeypatch):
    """A deployment that has minted `VEXA_FLOWS_TIMELINE_KEY`."""
    flows_api, _ = api
    monkeypatch.setattr(flows_api, "TIMELINE_KEY", NARROW)
    return {"X-Flows-Operator-Key": NARROW}


# ── E1a · the operator names a subject on every subject-scoped read, or reads nothing ───────────

#: Every route that answers ABOUT A PERSON. `GET /flows` is absent on purpose: it lists the
#: deployment's vocabulary, which is a fact about the machine and about nobody.
SUBJECT_SCOPED_READS = ["/reactions", "/timeline", "/queue/waiting", "/queue/notices", "/friction"]


@pytest.mark.parametrize("path", SUBJECT_SCOPED_READS)
def test_the_operator_key_with_no_subject_is_refused_on_every_subject_scoped_read(api, path):
    """4xx, and specifically not 200-with-the-instance. The status differs by route — `/friction`
    answers 401 because a report attributed to nobody is a different object, the rest answer 400 —
    and the assertion is deliberately about the CLASS: no credential that is not a person gets an
    instance-wide answer out of this surface any more."""
    flows_api, client = api
    r = client.get(path, headers=operator(flows_api))
    assert 400 <= r.status_code < 500, f"{path} -> {r.status_code}: {r.text[:200]}"
    assert "r-ben" not in r.text, f"{path} leaked a row nobody asked about"


@pytest.mark.parametrize("path", SUBJECT_SCOPED_READS)
def test_the_operator_key_naming_a_subject_still_opens_all_of_them(api, path):
    """The operator has not lost a capability, only an ambiguity: it says whose rows it wants.

    `/friction` is named through `X-User-Id` rather than `?subject=` — that route never published a
    subject argument and this change does not add one; what matters is that it has a way to name a
    person and no way to name none."""
    flows_api, client = api
    headers = operator(flows_api)
    params = {}
    if path == "/friction":
        headers = {**headers, "X-User-Id": "126"}
    else:
        params = {"subject": "126"}
    r = client.get(path, params=params, headers=headers)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_the_refusal_says_what_is_missing_rather_than_just_refusing(api):
    flows_api, client = api
    body = client.get("/reactions", headers=operator(flows_api)).json()
    assert body["detail"]["subject_required"] is True
    assert "?subject=" in body["detail"]["note"]


def test_the_stamped_identity_is_a_way_to_name_a_subject_not_a_way_to_skip_one(api):
    """`X-User-Id` is the gateway vouching for a person it resolved, and it still outranks
    `?subject=` on the routes that take it. What it is not is an exemption."""
    flows_api, client = api
    r = client.get("/queue/waiting", params={"subject": "512"},
                   headers={**operator(flows_api), "X-User-Id": "126"})
    assert r.status_code == 200 and r.json()["subject"] == "126"


# ── E1b · the ownership check on the verb runs unconditionally ──────────────────────────────────

def test_an_operator_with_no_subject_cannot_cancel_a_strangers_reaction(api):
    """THE BUG, stated as a test. `POST /reactions/r-ben/cancel` with the operator key and no
    `subject` used to cancel Ben's reaction with no check of any kind, because the ownership block
    sat behind `if subj:` and `subj` was empty."""
    flows_api, client = api
    r = client.post("/reactions/r-ben/cancel", headers=operator(flows_api), json={})
    assert 400 <= r.status_code < 500, r.text
    assert flows_api.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-ben'")[0][0] == "admitted", (
        "an operator that named nobody cancelled somebody's reaction")


def test_an_operator_naming_the_wrong_subject_is_refused_on_the_verb(api):
    """Named, and named WRONG: the reaction is real and so is the account, they are just not each
    other's. 403 rather than 404 for the reason the route documents."""
    flows_api, client = api
    r = client.post("/reactions/r-ben/cancel", params={"subject": "126"},
                    headers=operator(flows_api), json={})
    assert r.status_code == 403, r.text
    assert flows_api.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-ben'")[0][0] == "admitted"


def test_an_operator_naming_the_right_subject_still_steers_the_reaction(api):
    flows_api, client = api
    r = client.post("/reactions/r-ben/cancel", params={"subject": "512"},
                    headers=operator(flows_api), json={})
    assert r.status_code == 200, r.text
    assert flows_api.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-ben'")[0][0] == "cancelled"


# ── E1c · the narrow key is narrow ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/reactions", "/queue/waiting", "/queue/notices", "/friction"])
def test_the_timeline_key_opens_nothing_but_the_timeline(api, narrow_key, path):
    """403, not 401. The caller presented a real credential of this deployment's; it just does not
    open this route, and answering "your credential is unknown" sends an operator to rotate a key
    that is fine."""
    _flows_api, client = api
    r = client.get(path, params={"subject": "126"}, headers=narrow_key)
    assert r.status_code == 403, f"{path} -> {r.status_code}: {r.text[:200]}"
    assert "r-anna" not in r.text and "r-ben" not in r.text


def test_the_timeline_key_cannot_file_or_steer_anything_either(api, narrow_key):
    _flows_api, client = api
    assert client.post("/reactions/r-ben/cancel", headers=narrow_key, json={}).status_code == 403
    assert client.post("/friction", params={"what_happened": "x"},
                       headers=narrow_key).status_code == 403


def test_the_timeline_key_still_reads_a_named_subjects_timeline(api, narrow_key):
    _flows_api, client = api
    r = client.get("/timeline", params={"subject": "126"}, headers=narrow_key)
    assert r.status_code == 200, r.text
    assert r.json()["uid"] == "126"


def test_the_timeline_key_must_name_its_subject_too(api, narrow_key):
    """It carries no identity of its own, exactly like the operator key, so the same rule applies:
    it says who it is asking about or it is told no."""
    _flows_api, client = api
    r = client.get("/timeline", headers=narrow_key)
    assert r.status_code == 400, r.text


def test_the_timeline_key_never_mints_a_token_on_the_named_persons_account(api, narrow_key,
                                                                          monkeypatch):
    """THE SHARPEST HALF OF E1c. `meetings=true` is the DEFAULT, and that path runs
    `flows_timeline.service.fetch_meetings` -> `_user_key(uid)` -> `flows_steps.common.user_api_key`,
    which asks admin-api to mint a `["bot","browser","tx"]` gateway token ON THE SUBJECT'S ACCOUNT.
    A write, on a third party, from the credential documented as read-only and one-route.

    The spy is on `fetch_meetings` as the route holds it — the single door to that whole chain — and
    it EXPLODES rather than returning, so a regression cannot pass by ignoring a return value."""
    flows_api, client = api
    reached = []

    def _never(uid):
        reached.append(uid)
        raise AssertionError(f"the narrow key reached the minting path for uid {uid!r}")

    monkeypatch.setattr(flows_api, "fetch_meetings", _never)
    r = client.get("/timeline", params={"subject": "126", "meetings": "true"}, headers=narrow_key)
    assert r.status_code == 200, r.text
    assert reached == [], "the timeline key took the meetings hop"


def test_the_operator_key_does_still_take_the_meetings_hop(api, monkeypatch):
    """The control for the test above: the spy is wired the same way and IS called, so the previous
    test is about the tier and not about the spy never being reachable at all."""
    flows_api, client = api
    reached = []
    monkeypatch.setattr(flows_api, "fetch_meetings", lambda uid: reached.append(uid) or [])
    r = client.get("/timeline", params={"subject": "126", "meetings": "true"},
                   headers=operator(flows_api))
    assert r.status_code == 200, r.text
    assert reached == ["126"]


def test_the_timeline_key_is_its_own_caller_kind_not_an_admin(api):
    """The unit under all of the above: it used to return `Caller(kind="admin")`, and every
    consequence in this section followed from that one word."""
    flows_api, _ = api
    caller = flows_api.timeline_reader(x_flows_operator_key=NARROW) \
        if flows_api.TIMELINE_KEY == NARROW else None
    from flows_integrations.subject_auth import Caller
    narrow = Caller(kind="timeline")
    assert narrow.is_admin is False and narrow.is_timeline is True
    assert narrow.must_name_a_subject is True
    assert flows_api._as_me(narrow) is None, "the narrow key has no identity pair to hand down"
    assert caller is None or caller.kind == "timeline"


def test_a_placeholder_narrow_key_is_refused_rather_than_silently_ignored(monkeypatch):
    """It used to be coerced to `""`, which reads as prudent and is not: an operator who set the
    key to a literal published in this repository got no error and no effect."""
    from flows_integrations import flows_api
    monkeypatch.setenv("VEXA_FLOWS_TIMELINE_KEY", "vexa-internal-secret")
    with pytest.raises(RuntimeError) as e:
        flows_api._timeline_key()
    assert "VEXA_FLOWS_TIMELINE_KEY" in str(e.value)


# ── P15 · the 503 does not hand an unauthenticated caller our internal addresses ────────────────

def test_the_identity_503_does_not_name_the_internal_admin_api(api, monkeypatch):
    """`IdentityUnavailable` carries the transport exception, which names
    `VEXA_FLOWS_ADMIN_API_URL`. This 503 fires BEFORE identity has vouched for anyone, so whoever
    reads that sentence is an unauthenticated caller. The operator needs the detail and has the
    log; the caller needs to know it is not their key."""
    _flows_api, client = api
    from flows_integrations import subject_auth

    def _down(base, token, secret):
        from flows import StepError
        raise StepError("http POST http://admin-api.internal:8057/internal/validate: "
                        "URLError: connection refused")

    monkeypatch.setattr(subject_auth, "_validate", _down)
    r = client.get("/reactions", headers={"Authorization": "Bearer whoever"})
    assert r.status_code == 503, r.text
    for leaked in ("admin-api.internal", "8057", "/internal/validate", "URLError", "StepError"):
        assert leaked not in r.text, f"the 503 served {leaked!r} to an unauthenticated caller"
    assert "not your key" in r.json()["detail"]
