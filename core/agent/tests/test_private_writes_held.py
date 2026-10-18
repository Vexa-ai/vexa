"""The leak rule on WRITES: what a session read from the person's private sources does not reach a
page other people read unless the person confirms that one write (`control_plane.private_taint`).

From a real call: the person's agent searched their mail, then wrote what it found into a workspace
another member reads. Deny: a session that read a private source is refused a page write into a
workspace with other readers (409, a confirmation code, nothing on disk). Allow: the same write into
the person's own desk, a write from a session that read nothing private, and — once — the exact
write the person confirmed in their own chat message. The agent cannot confirm its own write.
"""
from __future__ import annotations

import pytest

from control_plane import identity_token, private_taint
from control_plane import workspace_membership as membership_mod
from tests.test_delegation_ceiling_verbs import KEY, PERSON, _build, _person

OTHER = "8"


def _worker(unit: str) -> dict:
    claims = {"sub": PERSON, "email": "ada@example.com",
              "delegation": {"regime": "human", "workspaces": "*", "unit": unit}}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


#: What the person's mail gave the session (friction 2, 2026-10-16: a write is held for what it
#: carries from the private sources, so the mail here holds the facts the writes below carry).
MAIL = {"messages": [{"id": "m1", "subject": "Invoice unpaid",
                      "snippet": "Venue Co: the meetup invoice is unpaid, €615 due Friday."}]}


def _fake_broker(actor, method, path, payload=None, *, identity):
    if path == "/api/connections":
        return {"connections": [{"id": "c1", "provider": "google_email", "status": "ready"}]}
    if path == "/api/preferences":
        return {}
    return MAIL


@pytest.fixture
def setup(monkeypatch, tmp_path):
    private_taint.reset()
    from control_plane.routers import connections as conn_mod  # noqa: PLC0415
    monkeypatch.setattr(conn_mod, "call_broker", _fake_broker)
    client = _build(monkeypatch, tmp_path)
    r = client.post("/api/workspace/shared/new", json={"name": "Meetup"}, headers=_person())
    assert r.status_code == 201, r.text
    ws = r.json()["workspace_id"]
    root = tmp_path / "workspaces"
    wsdir = membership_mod._ws_dir(root, ws)
    rows = membership_mod.read_members(root, ws) or [{"subject": PERSON, "role": "owner"}]
    rows.append({"subject": OTHER, "role": "contributor", "email": "val@example.com"})
    membership_mod._write_json_list(wsdir, membership_mod.MEMBERS_FILE, rows)
    yield client, ws, root
    private_taint.reset()


def _read_mail(client, unit):
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                headers=_worker(unit))


def _write(client, unit, ws, path="kg/entities/project/meetup.md", content="# Meetup\n\nInvoice unpaid: €615.\n"):
    return client.put("/api/workspace/file", json={"slug": ws, "path": path, "content": content},
                      headers=_worker(unit))


def test_a_session_that_read_nothing_private_writes_a_shared_page(setup):
    client, ws, _ = setup
    r = _write(client, "u-clean", ws)
    assert r.status_code == 200, r.text


def test_a_private_read_holds_the_write_into_a_shared_workspace(setup):
    client, ws, root = setup
    _read_mail(client, "u1")
    r = _write(client, "u1", ws)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["refused"] == "private_sources_to_shared"
    assert d["workspace"] == ws and d["sources"] == ["mail"]
    assert d["readers"] and len(d["confirm_code"]) == 6
    assert f"confirm {d['confirm_code']}" in d["instruction"]
    assert not list(root.rglob("meetup.md"))


def test_entity_upsert_is_held_the_same_way(setup):
    client, ws, _ = setup
    _read_mail(client, "u1")
    r = client.post("/api/workspace/entity", json={
        "slug": ws, "kind": "company", "name": "Venue Co", "facts": ["owes 615"], "source": "mail"},
        headers=_worker("u1"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["refused"] == "private_sources_to_shared"


def test_the_persons_own_desk_is_not_held(setup):
    client, _, _ = setup
    _read_mail(client, "u1")
    r = _write(client, "u1", PERSON, path="notes/mail.md")
    assert r.status_code == 200, r.text


def test_another_session_is_not_marked(setup):
    client, ws, _ = setup
    _read_mail(client, "u1")
    assert _write(client, "u2", ws).status_code == 200


def test_the_agent_cannot_confirm_its_own_write(setup):
    client, ws, _ = setup
    _read_mail(client, "u1")
    code = _write(client, "u1", ws).json()["detail"]["confirm_code"]
    client.post("/api/chat", json={"prompt": f"confirm {code}"}, headers=_worker("u1"))
    assert _write(client, "u1", ws).status_code == 409


def test_the_person_confirms_that_one_write_once(setup):
    client, ws, root = setup
    _read_mail(client, "u1")
    code = _write(client, "u1", ws).json()["detail"]["confirm_code"]
    client.post("/api/chat", json={"prompt": f"yes, confirm {code}"}, headers=_person())
    assert _write(client, "u1", ws).status_code == 200
    assert list(root.rglob("meetup.md"))
    # one confirmation, one write
    # (what the page already says is not held again: the next private detail is)
    assert _write(client, "u1", ws, content="# Meetup\n\nInvoice unpaid: €615.\nHost: Venue Co.\n").status_code == 409
    # a confirmation names one page: another page in the same workspace still waits
    assert _write(client, "u1", ws, path="other.md").status_code == 409



def test_the_held_answer_names_the_workspace_not_its_slug():
    """Founder 2026-10-16: the agent said "biz-b1c87fc32ecb". The instruction names the workspace."""
    from control_plane import private_taint
    r = private_taint.refusal(code="a8nv7c", workspace="biz-b1c87fc32ecb", path="plan.md", readers=["ana@x.io"],
                              found={"gmail"}, name="Business")
    assert "Business" in r["instruction"] and "biz-b1c87fc32ecb" not in r["instruction"]
    assert r["workspace"] == "biz-b1c87fc32ecb" and r["workspace_name"] == "Business"
