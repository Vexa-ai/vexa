"""WHAT THE ADMIN ACCOUNT IS FOR — the agent domain's half. Founder ruling 2026-10-11:

*"the global account functions: global flows setup via mcp … templates control (so they can control
seed templates that agent creates new notes from) … friction reading from sink … global workspace
setup"*.

Two of the four live here (flows/mail and friction are flows-api's, `core/flows/tests/
test_admin_account_mail.py`):

  TEMPLATES — `_global/kg/templates/<kind>.md` is the company's shape for new notes. The admin
  account writes it, through the page door, VALIDATED; every everyday account (the instance admin's
  included) is refused; a desk's `kg/templates/` stays refused for everybody; a template that would
  not work as a shape is refused on write and ignored on read; `entity_upsert` creates new cards
  from a valid one and never reshapes an existing page.

  WORKSPACE SETUP — the admin account's first sign-in opens `setup-global` over `_global` alone, and
  no admin ask still mounts a desk it does not have.

Each test fails on the code this branch was cut from: there, `kg/templates/` was refused to every
writer in every workspace, `entity_upsert` took no template, and the admin account's first visit
was the everyday one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import global_seed, kg_templates
from control_plane.api import _Sessions, create_app
from control_plane import workspace_membership as m
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests import admin_accounts
from tests.test_admin_account import (ADMIN_ACCOUNT, FOUNDER, MEMBER, _client, _h,  # noqa: F401
                                      stack)
from tests.test_api import _FakeIdentity, _FakeRuntime

REPO = Path(__file__).resolve().parents[3]
BAKED = REPO / "behavior" / "workspaces" / "default" / "kg" / "templates"
ASKS = REPO / "behavior" / "asks"


def _company_template(extra: str = "## Risk\n\n- <what could go wrong>\n\n") -> str:
    """The baked company template with one section added before the tail."""
    text = (BAKED / "company.md").read_text(encoding="utf-8")
    return text.replace("## Connected", extra + "## Connected", 1)


def _put(c, subject, path, content, slug="_global"):
    return c.put("/api/workspace/file", headers=_h(subject),
                 json={"path": path, "content": content, "slug": slug})


# ── the shape rule ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["person", "company", "meeting", "project", "decision"])
def test_every_baked_template_is_a_valid_company_template(kind):
    text = (BAKED / f"{kind}.md").read_text(encoding="utf-8")
    assert kg_templates.validate(f"kg/templates/{kind}.md", text) == []


def test_a_template_that_drops_a_section_the_writer_files_into_is_refused():
    broken = _company_template().replace("## Our relationship", "## Relationship")
    problems = kg_templates.validate("kg/templates/company.md", broken)
    assert problems and "Our relationship" in problems[-1]


@pytest.mark.parametrize("mutate,needle", [
    (lambda t: "> TEMPLATE banner\n" + t, "front matter"),
    (lambda t: t.replace("template: true", "template: false", 1), "template: true"),
    (lambda t: t.replace("type: company", "type: person", 1), "`type:`"),
    (lambda t: t.replace("## Timeline", "## Log", 1), "Timeline"),
])
def test_what_makes_a_template_unusable(mutate, needle):
    problems = kg_templates.validate("kg/templates/company.md", mutate(_company_template()))
    assert problems and any(needle in p for p in problems), problems


def test_a_file_that_names_no_kind_is_not_a_template():
    assert kg_templates.validate("kg/templates/vendor.md", "---\ntemplate: true\n---\n# x\n")
    assert kg_templates.validate("kg/templates/a/b.md", "---\ntemplate: true\n---\n")


# ── the door ─────────────────────────────────────────────────────────────────────────────────────

def test_the_admin_account_writes_a_company_template(stack):
    c = _client(stack)
    r = _put(c, ADMIN_ACCOUNT, "kg/templates/company.md", _company_template())
    assert r.status_code == 200, r.text
    on_disk = stack["root"] / "_global" / "kg" / "templates" / "company.md"
    assert "## Risk" in on_disk.read_text(encoding="utf-8")


def test_a_template_that_would_break_note_creation_is_refused_with_what_to_fix(stack):
    c = _client(stack)
    r = _put(c, ADMIN_ACCOUNT, "kg/templates/company.md",
             _company_template().replace("## Connected", "## Links", 1))
    assert r.status_code == 422
    assert any("Connected" in p for p in r.json()["detail"]["problems"])
    assert not (stack["root"] / "_global" / "kg" / "templates" / "company.md").exists()


@pytest.mark.parametrize("subject", [FOUNDER, MEMBER], ids=["the instance admin", "a member"])
def test_an_everyday_account_never_writes_a_company_template(stack, subject):
    c = _client(stack)
    assert _put(c, subject, "kg/templates/company.md", _company_template()).status_code == 403
    r = c.post("/api/workspace/remove", headers=_h(subject),
               json={"path": "kg/templates/company.md", "slug": "_global"})
    assert r.status_code == 403
    assert not (stack["root"] / "_global" / "kg" / "templates" / "company.md").exists()


def test_a_desks_templates_stay_refused_to_everybody(stack):
    c = _client(stack)
    assert _put(c, MEMBER, "kg/templates/company.md", _company_template(), slug="").status_code == 403


def test_a_template_cannot_be_moved_into_place(stack):
    """Only the validating door lands a template: a move would land a shape nobody checked."""
    c = _client(stack)
    assert _put(c, ADMIN_ACCOUNT, "drafts/company.md", _company_template()).status_code == 200
    r = c.post("/api/workspace/move", headers=_h(ADMIN_ACCOUNT), json={
        "path": "drafts/company.md", "to": "kg/templates/company.md", "slug": "_global"})
    assert r.status_code == 403


def test_removing_a_company_template_reverts_the_kind_to_the_baked_shape(stack):
    c = _client(stack)
    assert _put(c, ADMIN_ACCOUNT, "kg/templates/company.md", _company_template()).status_code == 200
    r = c.post("/api/workspace/remove", headers=_h(ADMIN_ACCOUNT),
               json={"path": "kg/templates/company.md", "slug": "_global"})
    assert r.status_code == 200, r.text
    assert kg_templates.company_template(stack["root"] / "_global", "company") == ""


# ── new notes take the company's shape ──────────────────────────────────────────────────────────

def _entity(c, subject, name, slug="_global", **extra):
    return c.post("/api/workspace/entity", headers=_h(subject), json={
        "slug": slug, "kind": "company", "name": name, "facts": ["It sells widgets."],
        "source": "test", **extra})


def test_a_new_company_page_is_created_from_the_company_template(stack):
    c = _client(stack)
    assert _put(c, ADMIN_ACCOUNT, "kg/templates/company.md", _company_template()).status_code == 200
    r = _entity(c, ADMIN_ACCOUNT, "Acme", section="Risk")
    assert r.status_code == 200, r.text
    assert "Risk" in r.json()["sections"] and r.json()["template"] == "company"
    page = (stack["root"] / "_global" / "kg" / "entities" / "company" / "acme.md").read_text()
    assert "## Risk" in page
    risk = page.split("## Risk", 1)[1].split("## ", 1)[0]
    assert "It sells widgets." in risk, "a fact filed into the added section did not land there"


def test_an_existing_page_is_never_reshaped(stack):
    c = _client(stack)
    assert _entity(c, ADMIN_ACCOUNT, "Acme").status_code == 200
    assert _put(c, ADMIN_ACCOUNT, "kg/templates/company.md", _company_template()).status_code == 200
    assert _entity(c, ADMIN_ACCOUNT, "Acme", facts=["It ships weekly."]).status_code == 200
    page = (stack["root"] / "_global" / "kg" / "entities" / "company" / "acme.md").read_text()
    assert "## Risk" not in page


def test_a_broken_template_on_disk_never_breaks_note_creation(stack):
    """The admin account's own agent can write `_global` through its mount, past the door."""
    c = _client(stack)
    d = stack["root"] / "_global" / "kg" / "templates"
    d.mkdir(parents=True)
    (d / "company.md").write_text("---\ntemplate: true\ntype: company\n---\n# nothing\n")
    r = _entity(c, ADMIN_ACCOUNT, "Acme")
    assert r.status_code == 200, r.text
    assert "template" not in r.json()
    page = (stack["root"] / "_global" / "kg" / "entities" / "company" / "acme.md").read_text()
    assert "## What it is" in page


def test_the_company_layer_is_seeded_with_the_templates_to_edit(tmp_path, monkeypatch):
    monkeypatch.setenv(global_seed.ENV_IMAGE_TEMPLATES_DIR, str(BAKED))
    added = global_seed.top_up(tmp_path, [(BAKED, global_seed.TEMPLATES_DIRNAME)])
    assert "kg/templates/company.md" in added
    edited = tmp_path / "kg" / "templates" / "company.md"
    edited.write_text(_company_template())
    assert global_seed.top_up(tmp_path, [(BAKED, global_seed.TEMPLATES_DIRNAME)]) == []
    assert "## Risk" in edited.read_text()


# ── workspace setup: the admin account's arrival ─────────────────────────────────────────────────

@pytest.fixture
def mint(tmp_path, monkeypatch, fake_admin_accounts):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    for name in ("first-visit", "setup-global"):
        (root / "_global" / "asks" / f"{name}.md").write_text((ASKS / f"{name}.md").read_text())
    subjects = {"admin@bank.test": ADMIN_ACCOUNT, "anna@bank.test": MEMBER}
    admin_accounts.name(ADMIN_ACCOUNT)
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     reader=WorkspaceReader(str(root)), sessions=_Sessions(),
                     membership_index=m.InMemoryMembershipIndex(),
                     email_subject_lookup=lambda a: subjects.get(str(a).lower()))
    client = TestClient(app)

    def go(who):
        r = client.post("/internal/scaffolds", headers={"X-Internal-Secret": "s"}, json={
            "who": who, "kind": "first-visit", "opening": "first-visit",
            "provenance": {"flow": "sign-in", "step": "first-visit"}})
        assert r.status_code == 201, r.text
        rec = client.get(f"/api/scaffolds/{r.json()['id']}",
                         headers={"X-User-Id": subjects[who], "X-User-Email": who})
        assert rec.status_code == 200, rec.text
        return rec.json()
    return go


def test_the_admin_accounts_first_sign_in_opens_the_company_setup(mint):
    rec = mint("admin@bank.test")
    assert rec["opening_preset"] == "setup-global"
    assert rec["workspaces"] == ["_global"]


def test_an_everyday_first_sign_in_is_unchanged(mint):
    rec = mint("anna@bank.test")
    assert rec["opening_preset"] == "first-visit"


@pytest.mark.parametrize("ask", ["setup-global", "flow-author", "policies-wizard", "mail-admin",
                                 "templates", "friction-review"])
def test_no_admin_ask_mounts_a_desk_the_admin_account_does_not_have(ask):
    head = (ASKS / f"{ask}.md").read_text(encoding="utf-8").split("---", 2)[1]
    mounts = next(line for line in head.splitlines() if line.startswith("mounts:"))
    assert mounts.split(":", 1)[1].strip() == "_global", (ask, mounts)


def test_setup_global_no_longer_writes_a_desk():
    text = (ASKS / "setup-global.md").read_text(encoding="utf-8")
    assert "TWO scaffolds" not in text
    assert "goes to their desk" not in text
