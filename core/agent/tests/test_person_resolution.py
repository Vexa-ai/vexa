"""D35 (PRD 0.13.4 S3): one Person, many addresses, on agent-api's side.

- The picker (``/api/people/mentionable``, ``/api/people/known``) lists a Person once, "2 accounts",
  and never names the account the viewer does not share a workspace with.
- A grant to the Person (a chat participant, a workspace member) reaches every linked account.
- No CRM, or a CRM that cannot answer: nothing is grouped and nobody extra is granted.

The CRM is a fake ``POST /internal/persons``; everything else is the real app over sqlite and git.
Synthetic accounts and ``*.test`` addresses only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane import person_resolution
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs
from tests.test_access_chat_mentions import (ALICE, DAVE, JOE, MAIL, SECRET, _FakeIdentity, _FakeReader,
                                             _FakeRuntime, _ws)

JOE_HOME = "u_joe_home"
MAIL_ALL = {**MAIL, JOE_HOME: "joe.home@mail.test"}
PERSON = "0f0e0d0c-0000-4000-8000-000000000001"


def _crm(calls, confirmed=True):
    """crm-api's /internal/persons for one human with two logins (joe, joe_home). ``confirmed``: the
    person confirmed the merge card from both logins; else the link is only the CRM's assertion."""
    def post(path, body):
        calls.append((path, body))
        out = {}
        for a in body["accounts"]:
            if a["subject"] in (JOE, JOE_HOME):
                out[a["subject"]] = {"person": PERSON, "accounts": [JOE, JOE_HOME],
                                     "confirmed": [JOE, JOE_HOME] if confirmed else [a["subject"]],
                                     "primary": MAIL_ALL[JOE_HOME], "merged": confirmed}
            else:
                out[a["subject"]] = {"person": None, "accounts": [a["subject"]], "confirmed": [a["subject"]],
                                     "primary": a["email"], "merged": False}
        return {"persons": out}
    return post


def _down(path, body):
    raise ConnectionError("crm unreachable")


def _app(tmp_path, monkeypatch, post):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setattr(person_resolution, "_default", person_resolution.Resolver(post=post) if post else
                        person_resolution.Resolver())
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    _ws(root, "acme", ALICE, idx, (JOE, "viewer"))
    _ws(root, "other", DAVE, idx)
    m.grant_membership(root, "other", JOE_HOME, "viewer", added_by=DAVE, index=idx, email=MAIL_ALL[JOE_HOME])
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"), internal_api_secret=SECRET)
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx,
                     email_subject_lookup=lambda a: {v: k for k, v in MAIL_ALL.items()}.get(str(a).lower()))
    return TestClient(app), store, root, idx


def _h(who):
    return {"X-User-Id": who, "X-User-Email": MAIL_ALL.get(who, "")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls: list = []
    client, store, root, idx = _app(tmp_path, monkeypatch, _crm(calls))
    yield client, store, root, idx, calls
    store.close()


# ── the picker ───────────────────────────────────────────────────────────────────────────────
def test_the_picker_lists_the_person_once_with_two_accounts(env):
    client, _store, _root, _idx, calls = env
    people = client.get("/api/people/mentionable", headers=_h(ALICE)).json()["people"]
    assert [p["subject"] for p in people] == [JOE]
    assert people[0]["accounts"] == 2 and people[0]["accounts_confirmed"] is True
    # the login alice shares nothing with is counted, never named: not its id, not its address
    text = json.dumps(people)
    assert JOE_HOME not in text and MAIL_ALL[JOE_HOME] not in text
    assert calls and calls[0][0] == "/internal/persons"


def test_the_people_strip_and_picker_set_carries_the_count():
    """``/api/people/known`` (the strip and the @ set) builds on the grouped colleagues: one row, the
    count, and a DM with the other login is the same person. (``*.test`` addresses are filtered from
    the strip as test accounts, so this one uses the reserved ``.invalid`` domain.)"""
    from control_plane import people
    rows = person_resolution.group_people(
        [{"subject": "11", "name": "Joe Example", "email": "joe@acme.invalid", "workspaces": ["acme"]},
         {"subject": "12", "name": "Joe Example", "email": "joe.home@mail.invalid", "workspaces": ["acme"]}],
        person_resolution.Resolver(post=lambda path, body: {"persons": {
            a["subject"]: {"person": PERSON, "accounts": ["11", "12"], "primary": "joe@acme.invalid"}
            for a in body["accounts"]}}))
    d = people.Directory("10", "me@acme.invalid", rows)
    out = people.known(dir=d, now=1000.0, dms=[{"id": "dm-1", "participants": ["10", "12"], "last_active": 900}],
                       meetings=[], shared=[])
    assert len(out) == 1 and out[0]["subject"] == "11" and out[0]["accounts"] == 2
    assert out[0]["how"] == ["chat", "workspace"]


def test_group_people_puts_the_primary_address_first_when_the_viewer_holds_both():
    resolver = person_resolution.Resolver(post=_crm([]))
    rows = [{"subject": JOE, "name": "Joe", "email": MAIL[JOE], "workspaces": ["acme"]},
            {"subject": JOE_HOME, "name": "Joe", "email": MAIL_ALL[JOE_HOME], "workspaces": ["home"]},
            {"subject": DAVE, "name": "Dave", "email": MAIL[DAVE], "workspaces": ["acme"]}]
    out = person_resolution.group_people(rows, resolver)
    assert [r["subject"] for r in out] == [JOE_HOME, DAVE]
    assert out[0]["linked"] == [JOE] and out[0]["accounts"] == 2
    assert sorted(out[0]["workspaces"]) == ["acme", "home"]
    assert "accounts" not in out[1]


# ── grants reach every linked account ────────────────────────────────────────────────────────
def test_sharing_a_chat_with_the_person_adds_every_linked_account(env):
    client, store, *_ = env
    sid = store.create(ALICE, name="pchat")["id"]
    r = client.post("/api/sessions/pchat/participants", json={"subject": JOE, "history": "all"}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    target = r.json()["session_id"]
    assert {p["principal"] for p in store.participants(target)} == {ALICE, JOE, JOE_HOME}
    assert sid


def test_adding_the_person_to_a_workspace_adds_every_linked_account(env):
    client, _store, root, *_ = env
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "acme", "person": JOE, "role": "write"}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    assert m.is_member(root, "acme", JOE) == "contributor"
    assert m.is_member(root, "acme", JOE_HOME) == "contributor"


def test_a_wrong_address_gets_nothing_before_the_person_confirms(tmp_path, monkeypatch):
    """Coordinator ruling 2026-10-17: before the merge card is confirmed from each login, the picker
    only hints "2 accounts?" and every grant reaches the account picked, nobody else."""
    client, store, root, _idx = _app(tmp_path, monkeypatch, _crm([], confirmed=False))
    people = client.get("/api/people/mentionable", headers=_h(ALICE)).json()["people"]
    assert people[0]["accounts"] == 2 and people[0]["accounts_confirmed"] is False
    store.create(ALICE, name="pchat")
    target = client.post("/api/sessions/pchat/participants", json={"subject": JOE, "history": "all"},
                         headers=_h(ALICE)).json()["session_id"]
    assert {p["principal"] for p in store.participants(target)} == {ALICE, JOE}
    client.post("/api/workspace/members/add", json={"workspace": "acme", "person": JOE, "role": "write"},
                headers=_h(ALICE))
    assert m.is_member(root, "acme", JOE) == "contributor" and m.is_member(root, "acme", JOE_HOME) is None
    store.close()


def test_primary_address_is_where_invites_and_nudges_go():
    resolver = person_resolution.Resolver(post=_crm([]))
    assert person_resolution.primary_address(JOE, MAIL[JOE], resolver=resolver) == MAIL_ALL[JOE_HOME]
    assert person_resolution.primary_address(DAVE, MAIL[DAVE], resolver=resolver) == MAIL[DAVE]
    # no CRM: the account's own address
    assert person_resolution.primary_address(JOE, MAIL[JOE], resolver=person_resolution.Resolver()) == MAIL[JOE]


# ── fails toward fewer ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("post", [None, _down])
def test_no_crm_or_a_crm_that_cannot_answer_groups_nothing_and_grants_nobody_extra(tmp_path, monkeypatch, post):
    client, store, root, _idx = _app(tmp_path, monkeypatch, post)
    people = client.get("/api/people/mentionable", headers=_h(ALICE)).json()["people"]
    assert [p["subject"] for p in people] == [JOE] and "accounts" not in people[0]
    store.create(ALICE, name="pchat")
    target = client.post("/api/sessions/pchat/participants", json={"subject": JOE, "history": "all"},
                         headers=_h(ALICE)).json()["session_id"]
    assert {p["principal"] for p in store.participants(target)} == {ALICE, JOE}
    client.post("/api/workspace/members/add", json={"workspace": "acme", "person": JOE, "role": "write"},
                headers=_h(ALICE))
    assert m.is_member(root, "acme", JOE_HOME) is None
    store.close()


def test_the_resolver_caches_and_is_off_without_a_url():
    calls: list = []
    r = person_resolution.Resolver(post=_crm(calls), clock=lambda: 0.0)
    r.persons([{"subject": JOE, "email": MAIL[JOE]}])
    r.persons([{"subject": JOE, "email": MAIL[JOE]}])
    assert len(calls) == 1
    assert person_resolution.Resolver().enabled is False
    assert person_resolution.Resolver("http://crm", "").enabled is False


# ── after the person merged: either address opens what is theirs ─────────────────────────────
def _merged_crm(merged):
    def post(path, body):
        return {"persons": {a["subject"]: {"person": PERSON, "accounts": [JOE, JOE_HOME],
                                           "primary": MAIL[JOE], "merged": merged} for a in body["accounts"]}}
    return post


@pytest.mark.parametrize("merged", [True, False])
def test_after_the_merge_each_account_opens_the_others_workspaces(tmp_path, monkeypatch, merged):
    client, store, root, _idx = _app(tmp_path, monkeypatch, _merged_crm(merged))
    assert m.is_member(root, "acme", JOE_HOME) is None and m.is_member(root, "other", JOE) is None
    r = client.post("/api/people/me/accounts-merged", headers=_h(JOE))
    assert r.status_code == 200, r.text
    assert r.json()["merged"] is merged
    if merged:
        assert m.is_member(root, "acme", JOE_HOME) == "viewer"
        assert m.is_member(root, "other", JOE) == "viewer"
        assert set(r.json()) == {"merged", "granted"}          # counts only, never names
    else:
        # no confirmed merge, no copy: the card is the person's, and only the card merges
        assert m.is_member(root, "acme", JOE_HOME) is None and m.is_member(root, "other", JOE) is None
    store.close()


def test_owning_a_workspace_is_never_copied_to_the_other_login(tmp_path, monkeypatch):
    client, store, root, idx = _app(tmp_path, monkeypatch, _merged_crm(True))
    _ws(root, "joes", JOE, idx)
    client.post("/api/people/me/accounts-merged", headers=_h(JOE_HOME))
    assert m.is_member(root, "joes", JOE) == "owner"
    assert m.is_member(root, "joes", JOE_HOME) == "contributor"    # reads and writes; owning stays put
    store.close()


def test_an_agent_without_a_person_cannot_trigger_the_copy(tmp_path, monkeypatch):
    from control_plane.identity_token import DELEGATION_HEADERS
    client, store, root, _idx = _app(tmp_path, monkeypatch, _merged_crm(True))
    r = client.post("/api/people/me/accounts-merged",
                    headers={**_h(JOE), DELEGATION_HEADERS["regime"]: "routine", DELEGATION_HEADERS["workspaces"]: "acme"})
    assert r.status_code == 403
    assert m.is_member(root, "acme", JOE_HOME) is None
    store.close()


# ── profiles: the claimed record's person-owned half, under the directory rule ───────────────
def _profiles_crm(calls):
    def post(path, body):
        calls.append(path)
        if path == "/internal/profiles":
            return {"profiles": {a["subject"]: {"display_name": "Joe", "headline": "Builds"} for a in body["accounts"]}}
        if path == "/internal/profiles/public":
            return {"handle": body["handle"], "fields": {"display_name": "Joe"}} if body["handle"] == "h" * 22 else {}
        return _crm([])(path, body)
    return post


def test_a_profile_is_read_under_the_directory_rule(tmp_path, monkeypatch):
    calls: list = []
    client, store, *_ = _app(tmp_path, monkeypatch, _profiles_crm(calls))
    r = client.get(f"/api/people/{JOE}/profile", headers=_h(ALICE)).json()
    assert r["profile"] == {"display_name": "Joe", "headline": "Builds"}
    # dave shares no workspace with alice: answered like someone with no profile, and the CRM is not asked
    before = len(calls)
    assert client.get(f"/api/people/{DAVE}/profile", headers=_h(ALICE)).json() == {"subject": DAVE, "profile": None}
    assert len(calls) == before
    assert client.get(f"/api/people/{ALICE}/profile", headers=_h(ALICE)).json()["profile"]["display_name"] == "Joe"
    store.close()


def test_the_public_page_needs_no_account_and_404s_unless_published(tmp_path, monkeypatch):
    client, store, *_ = _app(tmp_path, monkeypatch, _profiles_crm([]))
    assert client.get("/api/profiles/public/" + "h" * 22).json() == {"handle": "h" * 22, "fields": {"display_name": "Joe"}}
    assert client.get("/api/profiles/public/" + "x" * 22).status_code == 404
    store.close()
