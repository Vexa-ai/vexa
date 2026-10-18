"""Access vectors at flows-api's `POST /flows` for a person's own flows: the size of what one
submission may carry, and how many versions and drafts one person's scope may hold. Same offline
harness as `test_user_flows_api.py` (its fixtures are reused)."""
from __future__ import annotations

from test_user_flows_api import ROUTINE, as_, clean, client, fa  # noqa: F401 — fixtures

BIG = "x" * (fa.SUBMISSION_MAX_BYTES + 1)


def test_an_instruction_over_the_size_cap_is_refused(client):
    r = client.post("/flows", json={**ROUTINE, "params": {"instruction": BIG}},
                    headers=as_("anna-key"))
    assert r.status_code == 413 and r.json()["detail"]["refused"] == "submission_too_large"
    assert fa.db.execute("SELECT COUNT(*) FROM flow_version")[0][0] == 0


def test_params_over_the_size_cap_are_refused(client):
    r = client.post("/flows", json={**ROUTINE, "params": {"instruction": "brief me",
                                                           "note": BIG}},
                    headers=as_("anna-key"))
    assert r.status_code == 413 and r.json()["detail"]["refused"] == "submission_too_large"


def test_a_submission_under_the_cap_is_written(client):
    r = client.post("/flows", json={**ROUTINE, "params": {"instruction": "y" * 8000}},
                    headers=as_("anna-key"))
    assert r.status_code == 201, r.text


def test_drafts_are_capped_per_person(client, monkeypatch):
    monkeypatch.setattr(fa, "USER_SCOPE_MAX_DRAFTS", 2)
    codes = [client.post("/flows", json={**ROUTINE, "activate": False},
                         headers=as_("anna-key")).status_code for _ in range(3)]
    assert codes == [201, 201, 403]
    r = client.post("/flows", json={**ROUTINE, "activate": False}, headers=as_("anna-key"))
    assert r.json()["detail"]["refused"] == "quota_drafts"
    # another person's scope is untouched
    assert client.post("/flows", json={**ROUTINE, "activate": False},
                       headers=as_("ben-key")).status_code == 201


def test_versions_are_capped_per_person(client, monkeypatch):
    monkeypatch.setattr(fa, "USER_SCOPE_MAX_VERSIONS", 3)
    codes = [client.post("/flows", json=ROUTINE, headers=as_("anna-key")).status_code
             for _ in range(4)]
    assert codes == [201, 201, 201, 403]
    r = client.post("/flows", json=ROUTINE, headers=as_("anna-key"))
    assert r.json()["detail"]["refused"] == "quota_versions"
