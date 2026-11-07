"""Universal references (#1783, founder 2026-10-14: "references to workspaces, chats, documents,
etc., accepted everywhere where access is confirmed").

One format for every object — the URL ``https://<host>/r/<kind>/<id>`` and the chip
``[[kind:id|label]]`` — and ONE resolver on the server that answers for the CURRENT viewer:
readable → the object's label and where it opens; not theirs → "no access" with a request-access
offer and nothing else (no title, no name); gone → gone. A join link is a reference that carries a
grant. A ``[[Title]]`` chip resolves on the server too, so a name with accents finds its page.

L2: FastAPI over a sqlite session store, real git workspaces on disk, a list for a redis bus."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import join_links
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs
from workspaces.shared import links

ALICE, DAVE = "u_alice", "u_dave"
MAIL = {ALICE: "alice@acme.test", DAVE: "dave@acme.test"}
SECRET_TITLE = "Board prep merger 41"
WS_NAME = "Deal Room Zürich"
PAGE = "kg/entities/company/muhlemann-popp-ag.md"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Bus(list):
    def __call__(self, ch, payload):
        self.append((ch, json.loads(payload)))

    def to(self, who):
        return [f for c, f in self if c == f"u:{who}:notifications"]


def _h(who: str) -> dict:
    return {"X-User-Id": who, "X-User-Email": MAIL.get(who, "")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret="s",
                             global_system_workspace_path=str(root / "_global"),
                             ui_url="https://app.example.test")
    dispatcher = Dispatcher(settings, _Runtime(), _Identity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    bus = _Bus()

    def _meeting(user_id, meeting_id, workspaces=None):
        return {"id": int(meeting_id), "title": "Pricing call"} if user_id == ALICE else None

    app = create_app(dispatcher, reader=WorkspaceReader(str(root)), sessions=_Sessions(),
                     session_store=store, membership_index=idx, notify_publish=bus,
                     meeting_owner_lookup=_meeting,
                     email_subject_lookup=lambda a: {v: k for k, v in MAIL.items()}.get(str(a).lower()))
    c = TestClient(app)
    r = c.post("/api/workspace/shared/new", headers=_h(ALICE), json={"name": WS_NAME})
    assert r.status_code in (200, 201), r.text
    slug = r.json()["workspace_id"]                      # the directory; the stable id is the registry's
    wid = c.get(f"/api/workspaces/by-slug/{slug}", headers=_h(ALICE)).json()["id"]
    page = "---\ntitle: mühlemann+popp AG\nid: muhlemann-popp-ag\n---\n# mühlemann+popp AG\n"
    w = c.put("/api/workspace/file", headers=_h(ALICE), json={"path": PAGE, "content": page, "slug": slug})
    assert w.status_code == 200, w.text
    chat = store.create(ALICE, name="board", title=SECRET_TITLE)
    yield {"c": c, "store": store, "bus": bus, "root": root, "wid": wid, "slug": slug, "chat": chat["id"], "idx": idx}
    store.close()


def _resolve(c, who, ref):
    r = c.get("/api/refs/resolve", params={"ref": ref}, headers=_h(who))
    assert r.status_code == 200, r.text
    return r.json()


# ── the grammar ───────────────────────────────────────────────────────────────────────────────────

def test_one_grammar_for_every_kind_url_and_chip():
    for kind in ("workspace", "chat", "page", "entity", "meeting", "flow", "sensor"):
        url = links.ref_url(kind, "abc/def.md" if kind == "page" else "x1", base="https://app.example.test")
        assert url.startswith(f"https://app.example.test/r/{kind}/")
        got = links.parse_universal(url)
        assert got is not None and got.kind == kind
        chip = links.ref_chip(kind, got.id, "Shown")
        assert chip == f"[[{kind}:{got.id}|Shown]]"
        again = links.parse_universal(chip)
        assert (again.kind, again.id, again.label) == (kind, got.id, "Shown")
    assert links.parse_universal("[[mühlemann+popp AG]]") is None, "a title chip is not a typed ref"
    assert links.parse_universal("https://app.example.test/r/chat/../../etc") is None
    assert links.parse_universal("https://app.example.test/r/nothing/x") is None


def test_refs_are_found_in_any_text_a_calendar_body_or_a_mail():
    body = ("Agenda: see https://app.dev.vexa.ai/r/chat/c-123 and [[workspace:abcdefghij|Deal room]].\n"
            "Join: https://app.dev.vexa.ai/join/" + "A" * 43 + " — thanks")
    found = [(r.kind, r.id) for r in links.universal_refs_in(body)]
    assert ("chat", "c-123") in found and ("workspace", "abcdefghij") in found
    assert ("join", "A" * 43) in found
    mail = links.refs_as_urls("Notes in [[chat:c-123|the chat]].", base="https://app.example.test")
    assert mail == "Notes in the chat (https://app.example.test/r/chat/c-123)."


# ── one resolver, access for the current viewer ───────────────────────────────────────────────────

def test_workspace_readable_for_a_member_and_nothing_leaks_to_others(env):
    c, wid = env["c"], env["wid"]
    mine = _resolve(c, ALICE, f"https://app.example.test/r/workspace/{wid}")
    assert mine["access"] == "readable" and mine["label"] == WS_NAME and mine["url"] == f"/r/workspace/{wid}"
    theirs = _resolve(c, DAVE, f"[[workspace:{wid}|{WS_NAME}]]")
    assert theirs["access"] == "not-yours" and theirs["label"] is None and theirs["request_access"] is True
    assert WS_NAME not in json.dumps(theirs), "a label the writer put in the chip is never echoed back"
    assert _resolve(c, ALICE, "/r/workspace/zzzzzzzzzz")["access"] == "gone"


def test_chat_readable_for_its_people_only(env):
    c, chat = env["c"], env["chat"]
    mine = _resolve(c, ALICE, f"/r/chat/{chat}")
    assert mine["access"] == "readable" and mine["label"] == SECRET_TITLE and mine["open"] == {"chat": chat}
    theirs = _resolve(c, DAVE, f"/r/chat/{chat}")
    assert theirs["access"] == "not-yours" and theirs["request_access"] is True
    assert SECRET_TITLE not in json.dumps(theirs)
    assert _resolve(c, ALICE, "/r/chat/no-such-chat")["access"] == "gone"


def test_page_and_entity_by_id_with_a_unicode_name(env):
    c, wid = env["c"], env["wid"]
    page = _resolve(c, ALICE, f"/r/page/{wid}/{PAGE}")
    assert page["access"] == "readable" and page["label"] == "mühlemann+popp AG"
    assert page["open"]["path"] == PAGE
    ent = _resolve(c, ALICE, f"[[entity:{wid}/muhlemann-popp-ag]]")
    assert ent["access"] == "readable" and ent["open"]["path"] == PAGE
    theirs = _resolve(c, DAVE, f"/r/page/{wid}/{PAGE}")
    assert theirs["access"] == "not-yours" and "mühlemann" not in json.dumps(theirs)


def test_a_title_chip_with_accents_resolves_on_the_server(env):
    """The ü bug: the client built `m-hlemann-popp-ag` itself. The server's own slug finds the page."""
    c, slug = env["c"], env["slug"]
    r = c.post("/api/links/resolve", headers=_h(ALICE), json={"refs": ["mühlemann+popp AG"], "slug": slug})
    assert r.status_code == 200, r.text
    hit = r.json()["results"][0]
    assert hit["access"] == "readable" and hit.get("path") == PAGE


def test_meeting_through_the_one_meeting_access_decision(env):
    c = env["c"]
    mine = _resolve(c, ALICE, "/r/meeting/147")
    assert mine["access"] == "readable" and mine["label"] == "Pricing call" and mine["open"] == {"meeting": 147}
    theirs = _resolve(c, DAVE, "/r/meeting/147")
    assert theirs["access"] == "unavailable" and theirs["label"] is None and "Pricing" not in json.dumps(theirs)


def test_a_join_link_is_a_reference_that_carries_a_grant(env):
    c, root, slug = env["c"], env["root"], env["slug"]
    link = join_links.mint(root, "workspace", slug, role="viewer", created_by=ALICE)
    got = _resolve(c, DAVE, f"https://app.example.test/join/{link.token}")
    assert got["kind"] == "join" and got["access"] == "grant"
    assert got["open"] == {"join": link.token} and got["target"]["kind"] == "workspace"
    join_links.revoke(root, "workspace", slug, link.id)
    assert _resolve(c, DAVE, f"https://app.example.test/join/{link.token}")["access"] == "gone"


def test_an_unknown_kind_or_a_malformed_ref_is_a_calm_answer(env):
    c = env["c"]
    r = c.get("/api/refs/resolve", params={"ref": "hello there"}, headers=_h(ALICE))
    assert r.status_code == 400 and "reference" in r.json()["detail"]


def test_request_access_notifies_the_owner_and_tells_the_asker_nothing_more(env):
    c, chat, bus = env["c"], env["chat"], env["bus"]
    r = c.post("/api/refs/request-access", headers=_h(DAVE), json={"ref": f"/r/chat/{chat}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"requested": True}
    sent = bus.to(ALICE)
    assert sent and sent[-1]["notification"]["kind"] == "access_requested"
    assert sent[-1]["notification"]["object"] == {"kind": "chat", "id": chat, "name": SECRET_TITLE,
                                                   "url": f"/r/chat/{chat}"}
    # Nobody to ask for a ref the asker can already read, or one that is gone: refused calmly.
    assert c.post("/api/refs/request-access", headers=_h(ALICE),
                  json={"ref": f"/r/chat/{chat}"}).status_code == 409
    assert c.post("/api/refs/request-access", headers=_h(DAVE),
                  json={"ref": "/r/chat/no-such-chat"}).status_code == 404


# ── writing links: never into a workspace the page's readers cannot open (friction 2026-10-14) ──────

def test_a_shared_page_never_links_into_a_workspace_its_members_cannot_open(env):
    """entity_upsert into a NEW shared workspace rewrote `[[Name]]` to `[[ws:<private id>/…]]` because
    the person's page existed only in the writer's private workspace. The other member could not open
    it. The link stays local (`[[Name]]`) and the reader's resolver decides at read time."""
    c, root, idx = env["c"], env["root"], env["idx"]
    priv = c.post("/api/workspace/shared/new", headers=_h(ALICE), json={"name": "Alice only"}).json()["workspace_id"]
    w = c.post("/api/workspace/entity", headers=_h(ALICE), json={
        "kind": "person", "name": "Nora Quill", "facts": ["Runs pricing."], "source": "the pricing call", "slug": priv})
    assert w.status_code == 200, w.text
    shared = c.post("/api/workspace/shared/new", headers=_h(ALICE), json={"name": "With Dave"}).json()["workspace_id"]
    m.grant_membership(root, shared, DAVE, "viewer", added_by=ALICE, index=idx, email=MAIL[DAVE])
    r = c.post("/api/workspace/entity", headers=_h(ALICE), json={
        "kind": "company", "name": "Northwind Labs", "facts": ["Pricing owner is [[Nora Quill]]."], "source": "the pricing call", "slug": shared})
    assert r.status_code == 200, r.text
    page = (root / shared / "kg" / "entities" / "company" / "northwind-labs.md").read_text()
    assert "[[Nora Quill]]" in page
    assert "ws:" not in page, "a shared page linked into a workspace one of its members cannot open"


def test_a_private_page_may_still_link_by_id_into_a_workspace_its_reader_has(env):
    c, root = env["c"], env["root"]
    a = c.post("/api/workspace/shared/new", headers=_h(ALICE), json={"name": "Alpha"}).json()["workspace_id"]
    b = c.post("/api/workspace/shared/new", headers=_h(ALICE), json={"name": "Beta"}).json()["workspace_id"]
    assert c.post("/api/workspace/entity", headers=_h(ALICE), json={
        "kind": "person", "name": "Nora Quill", "facts": ["Runs pricing."], "source": "the pricing call", "slug": a}).status_code == 200
    r = c.post("/api/workspace/entity", headers=_h(ALICE), json={
        "kind": "company", "name": "Northwind Labs", "facts": ["Pricing owner is [[Nora Quill]]."], "source": "the pricing call", "slug": b})
    assert r.status_code == 200, r.text
    page = (root / b / "kg" / "entities" / "company" / "northwind-labs.md").read_text()
    assert "[[ws:" in page, "both workspaces have exactly the same reader: the id link is right"
