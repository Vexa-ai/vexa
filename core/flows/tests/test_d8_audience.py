"""DENY TEST 19 (PRD 0.13.4 §5.2, D8): a fact reaches its subject and its SEALED audience — nobody else.

"A card V decides produces a fact admitted to the waiting person's routine through audience, and to
no person outside the audience." The producer (crm) computes the audience and seals it into an
event.v2 envelope; flows verifies the seal and admits a person's flow when the person is the
subject (`refs.uid`) or in the audience. A caller cannot claim an audience: `_audience` in refs is
stripped, and an envelope that does not verify is a 403, never an unsigned admission. The seal is
bound to the fact it rides on and expires, so a sealed audience cannot be lifted onto another
subject, record or event, nor replayed later; and it is keyed by its own secret only.

OFFLINE: sqlite double, FakeClock, flows-api in process. Synthetic ids only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient  # noqa: E402
from fixtures import rig  # noqa: E402
from flows import admit  # noqa: E402
from flows import fact_envelope as fe  # noqa: E402
from flows.admission import audience_of, owner_of, reaches  # noqa: E402

# Same literals as test_sec_deny_2026_10_17.py: `flows_api` captures them at import and whichever
# module imports it first wins, so they must agree.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://subject-bearer:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

from sqlite_double import SqliteDB  # noqa: E402

if not isinstance(getattr(fa, "db", None), SqliteDB):
    fa.db = SqliteDB()

SEAL = "test-seal-secret"
V, D, X, W = "301", "302", "399", "ws:acme-sales"       # decider, waiting person, outsider, workspace
EVENT = "crm.record.changed"
REFS = {"org_id": "org-acme", "record_id": "card-7", "revision": 4, "uid": V}
ENVELOPE = {"v": 2, "producer": "crm", "object": "card", "owner": D, "visibility": "members",
            "audience": [V, D, W],
            "lifecycle": {"kind": "commitment", "state": "accepted", "next_actor": D,
                          "next_check_at": "2026-10-18T09:00:00+00:00", "handoff": "accepted"}}


def _seal(env=ENVELOPE, secret=SEAL, *, source_event_id, refs=REFS, event_type=EVENT, now=None):
    """The envelope sealed over the fact `{event_type, source_event_id, refs}` it is posted with."""
    return fe.seal(env, secret, fe.fact(event_type, source_event_id, refs), now=now)


def _body(source_event_id, refs=REFS, **seal):
    return {"event_type": EVENT, "source_event_id": source_event_id, "refs": refs,
            "envelope": _seal(source_event_id=source_event_id, refs=refs, **seal)}


# ── admission, in process ─────────────────────────────────────────────────────────────────────
def _people_rig():
    db, reg, clock, _ = rig()
    for owner in (D, X):
        reg.flow_by_names(name="card-watch", version=1, on_event=EVENT, step_names=["research_person"],
                          scope=f"user:{owner}")
    return db, reg, clock


def _admitted(db) -> set:
    return {r[0] for r in db.execute("SELECT flow FROM reaction")}


def test_a_sealed_audience_admits_the_waiting_person_and_nobody_outside_it():
    db, reg, clock = _people_rig()
    the_fact = fe.fact(EVENT, "e19", REFS)
    sealed = fe.verify(fe.seal(ENVELOPE, SEAL, the_fact), SEAL, {"crm"}, the_fact)
    assert admit(db, reg, clock, source_event_id="e19", event_type=EVENT, subject_refs=dict(REFS),
                 sealed=sealed) == 1
    assert _admitted(db) == {f"user:{D}/card-watch"}
    refs = __import__("json").loads(db.execute("SELECT subject_refs FROM reaction")[0][0])
    assert refs["_audience"] == [V, D, W] and refs["_owner"] == D and refs["_visibility"] == "members"
    assert refs["_lifecycle"]["state"] == "accepted"
    assert "title" not in refs and "summary" not in refs


def test_a_forged_audience_in_refs_admits_nobody():
    db, reg, clock = _people_rig()
    forged = {**REFS, "_audience": [X, D], "_owner": X, "_lifecycle": {"state": "x"}}
    assert admit(db, reg, clock, source_event_id="e19-forged", event_type=EVENT,
                 subject_refs=forged) == 0
    assert _admitted(db) == set()


def test_without_an_envelope_a_fact_reaches_its_subject_only():
    db, reg, clock = _people_rig()
    assert admit(db, reg, clock, source_event_id="e-old", event_type=EVENT,
                 subject_refs={**REFS, "uid": D}) == 1
    assert _admitted(db) == {f"user:{D}/card-watch"}


def test_a_workspace_principal_in_the_audience_never_matches_a_person():
    assert audience_of({"_audience": [W, D, "", 7]}) == [D, "7"]
    assert not reaches("acme-sales", {"_audience": [W]})
    assert not reaches(W, {"_audience": [W]})
    assert not reaches("", {"uid": ""})


def test_the_owner_is_the_sealed_owner_else_the_subject():
    assert owner_of({"uid": V, "_owner": D}) == D
    assert owner_of({"uid": V}) == V


# ── the intake, over HTTP ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("VEXA_FACT_SEAL_SECRET", SEAL)
    for owner in (D, X):
        fa.db.execute("DELETE FROM flow_version WHERE scope = :s", {"s": f"user:{owner}"})
        fa.db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
                      "status, created_by, created_at) VALUES (:s,'card-watch',1,:e,NULL,"
                      "'[\"agent_run\"]','{}','active','t',1.0)", {"s": f"user:{owner}", "e": EVENT})
    fa.db.execute("DELETE FROM reaction WHERE flow LIKE 'user:3%'")
    yield TestClient(fa.app, raise_server_exceptions=False)
    for owner in (D, X):
        fa.db.execute("DELETE FROM flow_version WHERE scope = :s", {"s": f"user:{owner}"})
    fa.db.execute("DELETE FROM reaction WHERE flow LIKE 'user:3%'")


KEY = {"X-Flows-Admin-Key": "test-flows-key"}


def _people_admitted() -> set:
    return {r[0] for r in fa.db.execute("SELECT flow FROM reaction WHERE flow LIKE 'user:3%'")}


def test_deny_19_a_card_v_decides_reaches_the_waiting_person_and_no_outsider(api):
    body = _body("d8-19-sealed")
    r = api.post("/events", json=body, headers=KEY)
    assert r.status_code == 202, r.text[:300]
    assert _people_admitted() == {f"user:{D}/card-watch"}


def test_deny_19_a_caller_claimed_audience_reaches_no_outsider(api):
    body = {"event_type": EVENT, "source_event_id": "d8-19-forged",
            "refs": {**REFS, "_audience": [X], "_owner": X, "_visibility": "members"}}
    r = api.post("/events", json=body, headers=KEY)
    assert r.status_code == 202, r.text[:300]
    assert f"user:{X}/card-watch" not in _people_admitted()
    assert _people_admitted() == set()


@pytest.mark.parametrize("tamper", [
    lambda e: {**e, "audience": e["audience"] + [X]},
    lambda e: {**e, "producer": "agent"},
    lambda e: {**e, "title": "Send the contract"},
    lambda e: {**e, "sig": "f" * 64},
], ids=["audience", "producer", "content-field", "sig"])
def test_deny_19_a_tampered_envelope_is_refused_403_and_admits_nobody(api, tamper):
    body = _body("d8-19-tampered")
    body["envelope"] = tamper(body["envelope"])
    r = api.post("/events", json=body, headers=KEY)
    assert r.status_code == 403, r.text[:300]
    assert r.json()["detail"] == {"refused": "envelope_unverified"}
    assert _people_admitted() == set()


def test_an_envelope_sealed_under_another_secret_is_refused(api, monkeypatch):
    body = _body("d8-other-secret", secret="not-the-deployment-secret")
    assert api.post("/events", json=body, headers=KEY).status_code == 403
    assert _people_admitted() == set()


def test_deny_with_no_seal_env_the_internal_secret_verifies_nothing(api, monkeypatch):
    """The seal secret is its own credential: a fact sealed under the internal-tier secret is
    refused when the seal secret is unset, and so is everything else."""
    monkeypatch.delenv("VEXA_FACT_SEAL_SECRET", raising=False)
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-internal-secret")
    for i, secret in enumerate(("test-internal-secret", SEAL)):
        r = api.post("/events", json=_body(f"d8-no-seal-env-{i}", secret=secret), headers=KEY)
        assert r.status_code == 403, r.text[:300]
    assert _people_admitted() == set()


@pytest.mark.parametrize("lift", [
    lambda b: {**b, "refs": {**b["refs"], "uid": X}},
    lambda b: {**b, "refs": {**b["refs"], "record_id": "card-8"}},
    lambda b: {**b, "refs": {**b["refs"], "revision": 5}},
    lambda b: {**b, "refs": {**b["refs"], "org_id": "org-other"}},
    lambda b: {**b, "source_event_id": b["source_event_id"] + "-again"},
], ids=["subject", "object-id", "revision", "org", "source-event-id"])
def test_deny_a_sealed_audience_lifted_onto_another_fact_is_refused(api, lift):
    """A valid seal for one card fact, reposted over a different subject, record, revision, org or
    event id, reaches nobody: the seal covers the fact it rides on."""
    r = api.post("/events", json=lift(_body("d8-lift")), headers=KEY)
    assert r.status_code == 403, r.text[:300]
    assert r.json()["detail"] == {"refused": "envelope_unverified"}
    assert _people_admitted() == set()


def test_deny_an_expired_seal_is_refused(api):
    import time
    stale = _body("d8-expired", now=time.time() - fe.SEAL_TTL_S - 1)
    r = api.post("/events", json=stale, headers=KEY)
    assert r.status_code == 403, r.text[:300]
    assert _people_admitted() == set()
