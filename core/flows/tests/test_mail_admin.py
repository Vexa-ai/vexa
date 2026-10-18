"""THE GLOBAL ACCOUNT DESIGNS THE MAIL, FROM CHAT — the `mail_*` tools' routes (founder 2026-10-14).

The whole loop, through flows-api, as the admin account:
  list kinds → edit the workspace-invite template → preview it → test-send it (to the admin's OWN
  address, nowhere else) → switch it on — and then a real invite carries the new wording.
Refusals: a person who is not the admin account; switching on an untested version (an edit after a
test-send needs a new one); switching the sign-in link off; a template that writes a URL.
Every person reads the invite address. The terminal's sign-in door is internal-tier only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import importlib  # noqa: E402

from flows_integrations.subject_auth import Caller  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from test_admin_account_mail import _app, _ENV  # noqa: E402,F401
from test_link_loop import FakeChannel  # noqa: E402

pytestmark = pytest.mark.mail_defaults

ADMIN = Caller(kind="subject", uid="1", email="admin@bank.test", instance_admin=True)
PERSON = Caller(kind="subject", uid="2", email="anna@bank.test")
SEED = (Path(__file__).resolve().parents[3] / "behavior" / "global" / "POLICIES.md")


def _mods():
    """The CURRENT copies — another suite in this run purges `flows_steps.*` from sys.modules, and
    flows-api imports what is current at call time."""
    names = ("common", "notify", "mailtext", "outbound", "policies")
    return [importlib.import_module(f"flows_steps.{n}") for n in names]


@pytest.fixture()
def world(_app, monkeypatch):
    global outbound, policies
    common, notify_mod, mailtext, outbound, policies = _mods()
    client, flows_api = _app
    flows_api.db = SqliteDB()
    store = {"POLICIES.md": SEED.read_text() if SEED.is_file() else ""}
    writes = []

    def read(uid, path, slug=None):
        return store.get(path) if slug in (None, "_global") or path == "POLICIES.md" else None

    def write(uid, path, content, slug=None):
        writes.append((uid, path, slug))
        store[path] = content
        return 200, {"path": path}
    monkeypatch.setattr(common, "ws_file", read)
    monkeypatch.setattr(common, "ws_write", write)
    monkeypatch.setattr(policies, "ws_file", read)
    monkeypatch.setattr(mailtext, "_global_file", lambda uid, path: store.get(path))
    monkeypatch.setenv("VEXA_UI_URL", "https://app.bank.test")
    ch = FakeChannel()
    notify_mod.use(ch)
    who = {"caller": ADMIN}
    flows_api.app.dependency_overrides[flows_api.subject_or_operator] = lambda: who["caller"]
    yield client, flows_api, store, ch, who, writes
    flows_api.app.dependency_overrides.clear()
    notify_mod.use(None)


def test_the_admin_account_designs_a_kind_end_to_end(world):
    client, flows_api, store, ch, who, writes = world
    kinds = {k["kind"]: k for k in client.get("/mail/kinds").json()["kinds"]}
    assert kinds["workspace-invite"]["switch"] == "on" and kinds["minutes"]["switch"] == "off"
    assert "inviter" in kinds["workspace-invite"]["variables"]

    r = client.put("/mail/templates/workspace-invite", json={"content": (
        "subject: {{inviter}} wants you in {{workspace_name}}\n---\n"
        "Hello! {{inviter}} added you to {{workspace_name}} at {{company}}.")})
    assert r.status_code == 200, r.text
    assert store["mail/workspace-invite.md"].startswith("subject: {{inviter}} wants you")
    assert writes[-1] == ("1", "mail/workspace-invite.md", "_global")
    v1 = r.json()["template_version"]

    pv = client.post("/mail/preview/workspace-invite", json={"sample": {"inviter": "Dima"}}).json()
    assert pv["subject"] == "Dima wants you in Sales desk" and pv["template_version"] == v1

    # off → on is refused before the test-send ...
    r = client.put("/mail/kinds/workspace-invite", json={"state": "on"})
    assert r.status_code == 409 and "test-sent" in r.json()["detail"]
    # ... the test-send goes to the admin's OWN address, and only there ...
    r = client.post("/mail/test-send/workspace-invite", json={})
    assert r.status_code == 200, r.text
    assert [m["to"] for m in ch.sent] == ["admin@bank.test"]
    assert ch.sent[0]["subject"].startswith("[test] ")
    # ... and then it switches on, written into _global/POLICIES.md
    r = client.put("/mail/kinds/workspace-invite", json={"state": "on"})
    assert r.status_code == 200, r.text
    assert "mail.workspace-invite: on" in store["POLICIES.md"]
    assert policies.resolve(store["POLICIES.md"])["mail.workspace-invite"] == "on"

    # a real invite now carries the new wording
    out = outbound.send("workspace-invite", "guest@partner.test", uid="1",
                        values={"inviter": "Dima", "workspace_name": "Collab", "role": "member",
                                "role_sentence": ""}, link="https://app/x", db=flows_api.db)
    assert out.sent and ch.sent[-1]["subject"] == "Dima wants you in Collab"

    # an edit after the test-send is a new version: switching on again needs a new test-send
    client.put("/mail/templates/minutes", json={"content": "subject: Minutes {{title}}\n---\nHi"})
    assert client.put("/mail/kinds/minutes", json={"state": "on"}).status_code == 409
    settings = client.get("/mail/kinds").json()["kinds"]
    assert {k["kind"]: k["switch"] for k in settings}["workspace-invite"] == "on"


def test_a_kind_left_off_sends_nothing(world):
    client, flows_api, store, ch, who, writes = world
    out = outbound.send("minutes", "anna@bank.test", uid="1", subject="s", body="b",
                        db=flows_api.db)
    assert not out.sent and ch.sent == []


def test_refusals(world):
    client, flows_api, store, ch, who, writes = world
    assert client.put("/mail/kinds/sign-in", json={"state": "off"}).status_code == 409
    r = client.put("/mail/templates/minutes",
                   json={"content": "subject: x\n---\nsee https://evil.example.com"})
    assert r.status_code == 422 and "URL" in r.json()["detail"]
    assert client.put("/mail/templates/rsvp", json={"content": "subject: x\n---\ny"}).status_code == 422
    assert client.get("/mail/kinds/nope").status_code in (404, 405)
    who["caller"] = PERSON
    for call in (lambda: client.get("/mail/kinds"),
                 lambda: client.post("/mail/test-send/minutes", json={}),
                 lambda: client.put("/mail/kinds/minutes", json={"state": "on"}),
                 lambda: client.put("/mail/templates/minutes", json={"content": "subject: a\n---\nb"})):
        assert call().status_code == 403
    assert ch.sent == []


def test_every_person_reads_the_invite_address(world, monkeypatch):
    client, flows_api, store, ch, who, writes = world
    who["caller"] = PERSON
    monkeypatch.setenv("VEXA_MAIL_ADDR", "info@vexa.ai")
    r = client.get("/mail/address")
    assert r.status_code == 200 and r.json()["address"] == "info@vexa.ai"


def test_the_sign_in_door_is_internal_tier_and_cannot_be_switched_off(world):
    client, flows_api, store, ch, who, writes = world
    store["POLICIES.md"] = "---\nmail.sign-in: off\n---\n"
    body = {"to": "anna@bank.test", "link": "https://app/redeem?t=x", "minutes": 15}
    assert client.post("/mail/sign-in", json=body).status_code == 403
    r = client.post("/mail/sign-in", json=body,
                    headers={"X-Internal-Secret": flows_api.INTERNAL_SECRET})
    assert r.status_code == 200, r.text
    assert ch.sent[-1]["link"] == "https://app/redeem?t=x"
    assert "15 minutes" in ch.sent[-1]["body"]


def test_a_platform_producer_sends_a_kind_through_the_same_path(world):
    client, flows_api, store, ch, who, writes = world
    body = {"kind": "workspace-joined", "to": "host@bank.test", "uid": "1",
            "values": {"joiner": "Anna", "workspace_name": "Collab"},
            "link": "https://app/w/collab"}
    assert client.post("/mail/send", json=body).status_code == 403
    hdr = {"X-Internal-Secret": flows_api.INTERNAL_SECRET}
    r = client.post("/mail/send", json=body, headers=hdr)
    assert r.status_code == 200 and r.json()["sent"] is True, r.text
    assert ch.sent[-1]["subject"] == "Anna joined Collab" and ch.sent[-1]["link"] == "https://app/w/collab"
    r = client.post("/mail/send", json={**body, "kind": "minutes"}, headers=hdr)
    assert r.status_code == 422
    store["POLICIES.md"] = "---\nmail.workspace-joined: off\n---\n"
    r = client.post("/mail/send", json=body, headers=hdr)
    assert r.status_code == 200 and r.json() == {**r.json(), "sent": False, "result": "off"}


def test_the_delivery_route_answers_captured_or_delivered(world, monkeypatch):
    client, flows_api, store, ch, who, writes = world
    monkeypatch.setenv("VEXA_MAIL_RELAY", "capture")
    assert client.get("/mail/delivery").json()["delivery"] == "captured"
    monkeypatch.setenv("VEXA_MAIL_RELAY", "relay")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "smtp.gmail.com")
    assert client.get("/mail/delivery").json()["delivery"] == "delivered"
    who["caller"] = PERSON
    r = client.post("/mail/send", json={"kind": "workspace-joined", "to": "h@bank.com",
                                        "values": {"joiner": "A", "workspace_name": "W"}},
                    headers={"X-Internal-Secret": flows_api.INTERNAL_SECRET})
    assert r.json()["delivery"] == "delivered"
