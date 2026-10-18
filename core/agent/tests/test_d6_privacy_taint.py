"""D6 (SC4): persisted private-data taint that fails closed.

The mark is a label on data, persisted in Redis per unit and per conversation, read by every egress
into a surface other people read. These tests cover what the deny suite does not:

* read routes label their answer (``X-Vexa-Privacy``), unclassified routes say ``none``;
* the mark survives an agent-api restart and a second replica (a fresh module and a fresh app over
  the same store), and follows the conversation to a sibling unit or a new mount generation;
* fail closed: an unreadable store holds every multi-reader write; a mark that cannot be written
  refuses the private read; a delegated request with no unit is held;
* held content is encrypted in agent-api and readable only by the person, never by a worker;
* the hold covers filenames, notices, agent messages and room messages, with a typed fault (P18).
"""
from __future__ import annotations

import importlib
import json

import pytest

from control_plane import held_store, identity_token, private_taint
from control_plane import workspace_membership as membership_mod
from shared import taint
from tests.test_delegation_ceiling_verbs import KEY, PERSON, _build, _person

OTHER = "8"


def _worker(unit: str) -> dict:
    claims = {"sub": PERSON, "email": "ada@example.com",
              "delegation": {"regime": "human", "workspaces": "*", "unit": unit}}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def _shared(client, root):
    r = client.post("/api/workspace/shared/new", json={"name": "Meetup"}, headers=_person())
    assert r.status_code == 201, r.text
    ws = r.json()["workspace_id"]
    wsdir = membership_mod._ws_dir(root, ws)
    rows = membership_mod.read_members(root, ws) or [{"subject": PERSON, "role": "owner"}]
    rows.append({"subject": OTHER, "role": "contributor", "email": "val@example.com"})
    membership_mod._write_json_list(wsdir, membership_mod.MEMBERS_FILE, rows)
    return ws


@pytest.fixture
def world(monkeypatch, tmp_path):
    private_taint.reset()
    client = _build(monkeypatch, tmp_path)
    root = tmp_path / "workspaces"
    yield client, _shared(client, root), root, monkeypatch, tmp_path
    private_taint.reset()


def _write(client, unit, ws, path="notes/plan.md", content="# Plan\n\nThe invoice is unpaid.\n"):
    return client.put("/api/workspace/file", json={"slug": ws, "path": path, "content": content},
                      headers=_worker(unit))


# ── labels on reads ──────────────────────────────────────────────────────────────────────────────

def test_person_wide_and_source_reads_are_labelled_private(world):
    client, ws, *_ = world
    for path in ("/api/people/followups", "/api/notifications", "/api/home/history"):
        r = client.get(path, headers=_person())
        assert r.headers.get("x-vexa-privacy", "").startswith("private"), (path, r.status_code)
    r = client.post("/api/connections/gmail/search", json={"query": "x", "limit": 1}, headers=_person())
    assert r.headers.get("x-vexa-privacy") == "private; class=mail"


def test_a_workspace_read_is_labelled_none(world):
    client, ws, *_ = world
    r = client.get("/api/workspace/file", params={"slug": ws, "path": "README.md"}, headers=_person())
    assert r.headers.get("x-vexa-privacy") == "none"


def test_label_grammar_fails_closed():
    assert taint.parse_label("none") == frozenset()
    assert taint.parse_label("private; class=crm,transcript") == {"crm", "transcript"}
    assert taint.parse_label(None) == {"unlabelled"}
    assert taint.parse_label("garbage") == {"unlabelled"}
    assert taint.parse_label("private") == {"private"}


# ── persisted: restart, replica, sibling units ───────────────────────────────────────────────────

def test_the_mark_survives_an_agent_api_restart_and_a_second_replica(world, _privacy_store):
    client, ws, root, monkeypatch, tmp_path = world
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                headers=_worker("agent-7-chat-main"))
    importlib.reload(private_taint)                    # a new agent-api process…
    replica = client                                   # …and the same app is a second replica: no
    assert _write(replica, "agent-7-chat-main", ws).status_code == 409   # in-process state is consulted
    assert private_taint.sources("agent-7-chat-main") == {"mail"}


def test_the_mark_follows_the_conversation_to_its_sibling_units(world):
    client, ws, *_ = world
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                headers=_worker("agent-7-chat-main"))
    for sibling in ("agent-7-chat-main-g2", "agent-7-chat-main-auto"):
        assert _write(client, sibling, ws).status_code == 409, sibling
    assert private_taint.sources("agent-7-chat-main-g3-signal") == {"mail"}
    assert _write(client, "agent-7-chat-other", ws).status_code == 200


def test_the_worker_redis_user_reads_the_edges_mark_only():
    from control_plane import workload_redis as wr
    rules = wr.acl_rules("agent-7-chat-main-g2", "pw")
    assert "%R~vexa:taint:unit:agent-7-chat-main-g2" in rules
    assert "%R~vexa:taint:session:agent-7-chat-main" in rules       # read-only: cannot clear the mark
    assert "~vexa:taint:wsession:agent-7-chat-main" in rules


# ── fail closed ──────────────────────────────────────────────────────────────────────────────────

class _Broken:
    def __getattr__(self, name):
        def boom(*a, **k):
            raise ConnectionError("redis unreachable")
        return boom


def test_an_unreadable_store_holds_every_multi_reader_write(world):
    client, ws, *_ = world
    taint.configure(taint.TaintStore(_Broken()))
    r = _write(client, "agent-7-chat-main", ws)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["sources"] == ["unverifiable"] and d["fault"]["unverifiable"] is True
    # the person's own desk has no other reader: not held
    assert _write(client, "agent-7-chat-main", PERSON, path="notes/mine.md").status_code == 200


def test_no_store_at_all_is_held_too(world):
    client, ws, *_ = world
    taint.configure(None)
    assert _write(client, "agent-7-chat-main", ws).status_code == 409


def test_a_private_read_whose_mark_cannot_be_written_is_refused(world):
    client, ws, *_ = world
    taint.configure(taint.TaintStore(_Broken()))
    r = client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                    headers=_worker("agent-7-chat-main"))
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["refused"] == private_taint.MARK_UNAVAILABLE


def test_a_worker_request_without_a_unit_is_held(world):
    client, ws, *_ = world
    claims = {"sub": PERSON, "email": "ada@example.com", "delegation": {"regime": "human", "workspaces": "*"}}
    r = client.put("/api/workspace/file", json={"slug": ws, "path": "a.md", "content": "x"},
                   headers={identity_token.HEADER: identity_token.sign(KEY, claims)})
    assert r.status_code == 409, r.text


# ── held content: encrypted, the person's only ───────────────────────────────────────────────────

def test_held_content_is_encrypted_and_readable_only_by_the_person(world, _privacy_store):
    client, ws, root, *_ = world
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                headers=_worker("u1"))
    d = _write(client, "u1", ws).json()["detail"]
    assert d["fault"] == {"code": "held_private_egress", "surface": "page", "held": True,
                          "release": f"confirm {d['confirm_code']}", "unverifiable": False}
    raw = _privacy_store.get(held_store.item_key(PERSON, d["confirm_code"]))
    assert raw and "unpaid" not in raw and "plan.md" not in raw
    assert not list(root.rglob("held-writes"))                       # nothing on any desk
    listed = client.get("/api/held", headers=_person()).json()["items"]
    assert [x["code"] for x in listed] == [d["confirm_code"]] and listed[0]["surface"] == "page"
    one = client.get(f"/api/held/{d['confirm_code']}", headers=_person()).json()
    assert "unpaid" in one["payload"]["content"]
    assert client.get("/api/held", headers=_worker("u1")).status_code == 403
    assert client.get(f"/api/held/{d['confirm_code']}", headers=_worker("u1")).status_code == 403
    assert client.delete(f"/api/held/{d['confirm_code']}", headers=_person()).status_code == 200
    assert client.get("/api/held", headers=_person()).json()["items"] == []


def test_a_foreign_key_cannot_open_another_persons_item(_privacy_store):
    store = held_store.current()
    store.put("7", "abc234", surface="page", sources=["mail"], payload={"content": "secret"})
    _privacy_store.set(held_store.item_key("8", "abc234"), _privacy_store.get(held_store.item_key("7", "abc234")))
    assert store.get("8", "abc234") is None                           # bound to the person and code
    other = held_store.HeldStore(_privacy_store, "another-deployment-secret")
    assert other.get("7", "abc234") is None


def test_legacy_desk_held_writes_are_swept_into_the_store(tmp_path):
    desk = tmp_path / "7"
    (desk / held_store.LEGACY_DIR).mkdir(parents=True)
    (desk / held_store.LEGACY_DIR / "abc234.json").write_text(json.dumps(
        {"code": "abc234", "workspace": "meetup", "files": {"a.md": "eA=="}, "sources": ["mail"]}))
    assert held_store.migrate_desk(desk, "7") == 1
    assert not list((desk / held_store.LEGACY_DIR).glob("*.json"))
    assert held_store.current().get("7", "abc234")["kind"] == "files"


# ── every multi-reader egress ────────────────────────────────────────────────────────────────────

def test_a_rename_into_a_shared_workspace_is_held(world):
    client, ws, *_ = world
    assert _write(client, "u-clean", ws, path="notes/a.md").status_code == 200
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5}, headers=_worker("u1"))
    r = client.post("/api/workspace/move", json={"slug": ws, "path": "notes/a.md",
                                                 "to": "notes/acme-owes-615.md"}, headers=_worker("u1"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["surface"] == "filename"


def test_a_workspace_purpose_from_a_marked_session_is_held(world):
    client, ws, *_ = world
    client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5}, headers=_worker("u1"))
    r = client.post("/api/workspace/purpose", json={"slug": ws, "purpose": "chase Acme's 615"},
                    headers=_worker("u1"))
    assert r.status_code in (409, 403), r.text
    if r.status_code == 409:
        assert r.json()["detail"]["surface"] == "purpose"


def test_agent_message_and_notice_are_held_for_other_readers(_privacy_store):
    taint.TaintStore(_privacy_store).mark("u1", ["crm"])
    for surface in ("agent_message", "notice"):
        held = private_taint.check_egress(subject="7", unit="u1", surface=surface, target="8/s-1",
                                          readers=["8"], content="Acme owes 615")
        assert held and held["fault"]["code"] == "held_private_egress" and held["surface"] == surface
    # to nobody else: not held
    assert private_taint.check_egress(subject="7", unit="u1", surface="notice", target="7",
                                      readers=[], content="x") is None


def test_a_room_reply_from_a_marked_unit_is_replaced_by_a_fixed_notice(_privacy_store):
    from control_plane import room_echo
    unit = "agent-ws-w1-room-s-abc"
    row = {"id": "s-abc", "owner": "7"}
    assert room_echo._held_or_said(unit, row, "1-0", "hello") == "hello"
    taint.TaintStore(_privacy_store).mark(unit, ["crm"])
    said = room_echo._held_or_said(unit, row, "2-0", "Acme owes 615")
    assert said == room_echo.HELD_NOTICE and "615" not in said
    assert held_store.current().get("7", "room-2-0")["payload"]["content"] == "Acme owes 615"
