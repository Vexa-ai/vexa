"""ADDING A MEMBER IS A CONVERSATION — the server half (Vexa-ai/vexa#1632).

The front page's three controls queue an act on the chat; the agent asks, confirms, and calls one of
two verbs. This is what those verbs reach: the gate (owner-only · `_system` never · `_global`
admin-only), the three roles, the commit with the inviter as its author, and the two ways an invite
link gets to a person — mailed for an address this instance does not know, handed back for one it
does.

Offline L2, over the same fakes Lane M uses: a real git workspace on disk, an in-memory membership
index, and injected callables for the two things that leave the process (identity's address lookup,
the flows publish). Nothing here needs docker, a runtime or a DB.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import membership_acts as acts
from control_plane import publish as publish_mod
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

OWNER = "u_owner"
OWNER_MAIL = "owner@example.test"
NEW_MAIL = "jsmith@example.com"
WS = "pilot"


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, workspace_id: str = WS) -> Path:
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _owned(root: Path, workspace_id: str = WS, idx=None):
    """A shared workspace with one owner — the state every act below starts from."""
    _init_ws(root, workspace_id)
    idx = idx or m.InMemoryMembershipIndex()
    m.ensure_owner(root, workspace_id, OWNER, index=idx, email=OWNER_MAIL,
                   commit_fn=m.policy_commit)
    return idx


def _settings(root: Path, *, admins: str = "", ui: str = "https://app.example.test"):
    return load_settings(workspaces_dir=str(root),
                         global_system_workspace_path=str(root / "_global"),
                         global_admin_subjects=admins,
                         internal_api_secret="s", ui_url=ui, redis_url="")


def _client(root: Path, *, admins: str = "", index=None, known=None) -> TestClient:
    """`known` is the address book this deployment holds — an address in it is INTERNAL."""
    settings = _settings(root, admins=admins)
    return TestClient(create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
        email_subject_lookup=lambda address: (known or {}).get(str(address).lower()),
    ))


def _h(subject: str, email: str = "") -> dict:
    h = {"X-User-Id": subject}
    if email:
        h["X-User-Email"] = email
    return h


# ── the three roles replace the one ──────────────────────────────────────────────────────────────
def test_the_roles_are_owner_contributor_reader():
    """The whole of deliverable 3. `INVITABLE_ROLES` was `("contributor",)`, which is the tuple the
    founder read back off a button that could not work."""
    assert m.INVITABLE_ROLES == ("owner", "contributor", "reader")
    assert set(m.ROLE_SENTENCES) == set(m.ROLE_WORDS)


def test_reader_is_viewer_on_disk_and_reader_to_a_person():
    """One rank, two spellings, and the normalisation is the only place that knows."""
    assert m.normalize_role("reader") == "viewer"
    assert m.normalize_role("VIEWER") == "viewer"
    assert m.normalize_role(" Contributor ") == "contributor"
    assert m.role_word("viewer") == "reader"
    assert m.role_word("owner") == "owner"


def test_an_unknown_role_is_refused_with_the_three_and_what_each_means():
    with pytest.raises(m.MembershipError) as e:
        m.normalize_role("editor")
    said = str(e.value)
    for word in m.ROLE_WORDS:
        assert word in said
    assert "reads this group and does not write it" in said
    assert "('contributor',)" not in said       # never a python repr in front of a person


def test_every_role_has_a_sentence_and_it_is_the_one_the_prompt_ships():
    """The worker's prompt retypes these (it ships in its own image and this module is not
    importable there). Pinned together so a rewrite in one cannot quietly disagree with the other."""
    from worker.engine import member_verbs_preamble
    said = member_verbs_preamble()
    for word in m.ROLE_WORDS:
        assert m.ROLE_SENTENCES[word] in said, word
    assert "workspace_invite(slug, email, role)" in said
    assert "workspace_membership(slug, email, role)" in said


def test_the_prompt_names_the_verbs_beside_workspace_write():
    """Deliverable 4, literally: the membership verbs ride the SAME preamble as the page verbs, so a
    turn cannot read one and miss the other."""
    from worker.engine import page_verbs_preamble
    said = page_verbs_preamble()
    assert "workspace_write(path, content, slug)" in said
    assert "workspace_invite(slug, email, role)" in said
    assert "workspace_membership(slug, email, role)" in said


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────
def test_only_an_owner_may_change_who_is_in_a_workspace(tmp_path):
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_contrib", "contributor", added_by=OWNER, index=idx)
    acts.assert_may_manage(tmp_path, WS, OWNER, is_admin=False)          # the owner passes
    for who in ("u_contrib", "u_stranger"):
        with pytest.raises(acts.ActRefused) as e:
            acts.assert_may_manage(tmp_path, WS, who, is_admin=False)
        assert e.value.status == 403


def test_the_private_tier_is_refused_for_everybody_including_the_admin(tmp_path):
    for slug in ("_system", "sys", "system", ".attached"):
        for admin in (False, True):
            with pytest.raises(acts.ActRefused) as e:
                acts.assert_may_manage(tmp_path, slug, OWNER, is_admin=admin)
            assert e.value.status == 403
            assert "nobody's to share" in str(e.value)


def test_the_company_layer_is_admin_only_and_then_says_what_it_actually_is(tmp_path):
    """The two answers are DIFFERENT answers. A non-admin lacks the permission; the admin holds it
    and is told that the editor set is a named list in POLICIES.md rather than a membership."""
    with pytest.raises(acts.ActRefused) as not_admin:
        acts.assert_may_manage(tmp_path, "_global", "u_someone", is_admin=False)
    assert not_admin.value.status == 403
    assert "administrator" in str(not_admin.value)

    with pytest.raises(acts.ActRefused) as admin:
        acts.assert_may_manage(tmp_path, "_global", OWNER, is_admin=True)
    assert admin.value.status == 409
    assert "POLICIES.md" in str(admin.value)
    assert "global_admin_only" in str(admin.value)


def test_an_act_with_no_workspace_named_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(acts.ActRefused) as e:
        acts.assert_may_manage(tmp_path, "", OWNER, is_admin=False)
    assert e.value.status == 400


# ── the invite ───────────────────────────────────────────────────────────────────────────────────
def test_an_external_address_is_mailed_through_the_carrier(tmp_path):
    idx = _owned(tmp_path)
    sent: list = []
    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="contributor", inviter=OWNER,
                      inviter_email=OWNER_MAIL, workspace_name="Pilot", index=idx,
                      ui_url="https://app.example.test",
                      commit_fn=m.policy_commit_as(OWNER, OWNER_MAIL),
                      resolve_subject=lambda a: None,
                      mail=lambda fact: (sent.append(fact), True)[1])
    assert out["delivery"] == "mailed" and out["internal"] is False
    assert out["role"] == "contributor"
    assert out["role_sentence"] == m.ROLE_SENTENCES["contributor"]
    fact = sent[0]
    assert fact["email"] == NEW_MAIL and fact["workspace"] == WS
    assert fact["link"] == out["link"] and fact["link"].startswith("https://app.example.test/join?i=")
    assert fact["uid"] == OWNER and fact["inviter"] == OWNER_MAIL


def test_an_address_this_instance_knows_gets_the_link_and_no_mail(tmp_path):
    idx = _owned(tmp_path)
    sent: list = []
    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                      ui_url="https://app.example.test",
                      resolve_subject=lambda a: "u_them",
                      mail=lambda fact: (sent.append(fact), True)[1])
    assert sent == []                       # nothing published for somebody already here
    assert out["delivery"] == "link" and out["internal"] is True
    assert out["link"].startswith("https://app.example.test/join?i=")


def test_a_publish_that_did_not_land_says_the_link_is_still_ours_to_give(tmp_path):
    """A dropped publish is not an error and must not be reported as a mail. The invite store
    holds only the hash, so the plaintext link exists exactly once — in this answer."""
    idx = _owned(tmp_path)
    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                      ui_url="https://app.example.test",
                      resolve_subject=lambda a: None, mail=lambda fact: False)
    assert out["delivery"] == "link"
    assert out["link"]


def test_the_invite_is_restricted_to_the_address_it_names(tmp_path):
    """A forwarded link grants nobody anything — the difference between naming a person and the old
    anyone-with-link button."""
    idx = _owned(tmp_path)
    acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                ui_url="https://app.example.test", commit_fn=m.policy_commit,
                resolve_subject=lambda a: None)
    rec = json.loads(m.invites_path(tmp_path, WS).read_text())[0]
    assert rec["mode"] == "restricted" and rec["allowed_emails"] == [NEW_MAIL]
    assert rec["max_uses"] == 1 and rec["role"] == "viewer"      # stored spelling
    assert "token" not in rec                                    # still hash-only


def test_the_invite_leaves_the_workspace_repo_alone(tmp_path):
    """THE STORE IS NOT IN THE TREE (Vexa-ai/vexa#1645). Minting used to write and commit
    `policy/invites.json` inside the workspace — the same directory the worker's turn runs in — and
    the turn's write-back deleted it a second later. A mint now touches no path under the workspace
    at all, so no writer of that tree can take it away again."""
    idx = _owned(tmp_path)
    ws = tmp_path / WS
    head_before = _git(ws, "rev-parse", "HEAD")

    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER,
                      inviter_email=OWNER_MAIL, index=idx, ui_url="https://app.example.test",
                      commit_fn=m.policy_commit_as(OWNER, OWNER_MAIL),
                      resolve_subject=lambda a: None)

    assert out["invited"] is True
    assert _git(ws, "rev-parse", "HEAD") == head_before, "a mint must not commit to the workspace"
    assert _git(ws, "status", "--porcelain") == "", "a mint must not dirty the workspace tree"
    assert not (ws / m.LEGACY_INVITES_FILE).exists()
    # and it IS persisted — outside every workspace mount, where preview and accept read it
    assert m.invites_path(tmp_path, WS).exists()
    assert m.preview_invite(tmp_path, out["link"].split("i=")[1])["workspace_id"] == WS


def test_the_membership_change_is_a_commit_authored_by_the_actor(tmp_path):
    """The #1632 authorship property, on the write that still lands in the workspace: the ROSTER.
    `policy/members.json` is workspace knowledge and stays in the tree, so *who changed who is here*
    is answerable from `git log --format=%an` rather than by reading a JSON diff."""
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "viewer", added_by=OWNER, index=idx, email=NEW_MAIL,
                       commit_fn=m.policy_commit)
    acts.set_membership(tmp_path, WS, email=NEW_MAIL, role="contributor", actor=OWNER, index=idx,
                        commit_fn=m.policy_commit_as(OWNER, OWNER_MAIL))
    ws = tmp_path / WS
    assert _git(ws, "log", "-1", "--format=%an") == OWNER
    assert _git(ws, "log", "-1", "--format=%ae") == OWNER_MAIL
    assert _git(ws, "log", "-1", "--format=%cn") == "vexa-platform"   # committer stays the platform
    assert "policy/members.json" in _git(ws, "show", "--name-only", "--format=", "HEAD")


def test_an_address_that_is_not_one_is_refused_before_a_token_exists(tmp_path):
    idx = _owned(tmp_path)
    for bad in ("jsmith", "jsmith@", "@example.com", "", "  "):
        with pytest.raises(acts.ActRefused) as e:
            acts.invite(tmp_path, WS, email=bad, role="reader", inviter=OWNER, index=idx,
                        ui_url="https://app.example.test", resolve_subject=lambda a: None)
        assert e.value.status == 400
    assert not m.invites_path(tmp_path, WS).exists()


def test_no_ui_url_means_no_invite_rather_than_a_link_to_nowhere(tmp_path):
    idx = _owned(tmp_path)
    with pytest.raises(acts.ActRefused) as e:
        acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                    ui_url="", resolve_subject=lambda a: None)
    assert e.value.status == 503 and "VEXA_UI_URL" in str(e.value)


def test_inviting_somebody_already_here_says_what_they_are_instead(tmp_path):
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "contributor", added_by=OWNER, index=idx,
                       email=NEW_MAIL)
    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                      ui_url="https://app.example.test", resolve_subject=lambda a: "u_them")
    assert out["already_member"] is True and out["invited"] is False
    assert out["role"] == "contributor"
    assert not m.invites_path(tmp_path, WS).exists()


# ── the membership change ────────────────────────────────────────────────────────────────────────
def test_a_role_is_changed_by_the_address_the_roster_shows(tmp_path):
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "contributor", added_by=OWNER, index=idx,
                       email=NEW_MAIL)
    out = acts.set_membership(tmp_path, WS, email=NEW_MAIL, role="reader", actor=OWNER, index=idx,
                              commit_fn=m.policy_commit_as(OWNER, OWNER_MAIL))
    assert out["role"] == "reader" and out["removed"] is False
    assert m.is_member(tmp_path, WS, "u_them") == "viewer"
    assert _git(tmp_path / WS, "log", "-1", "--format=%an") == OWNER


def test_remove_is_a_role_value_and_takes_them_off(tmp_path):
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "contributor", added_by=OWNER, index=idx,
                       email=NEW_MAIL)
    out = acts.set_membership(tmp_path, WS, email=NEW_MAIL, role="remove", actor=OWNER, index=idx,
                              commit_fn=m.policy_commit)
    assert out["removed"] is True
    assert m.is_member(tmp_path, WS, "u_them") is None
    assert idx.list("u_them") == []


def test_a_member_the_roster_has_no_email_for_is_still_reachable(tmp_path):
    """Rows granted before emails were stored have no address. Identity answers for them, and the
    act must not decide they are not here."""
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "contributor", added_by=OWNER, index=idx)
    out = acts.set_membership(tmp_path, WS, email=NEW_MAIL, role="reader", actor=OWNER, index=idx,
                              resolve_subject=lambda a: "u_them", commit_fn=m.policy_commit)
    assert out["role"] == "reader" and out["subject"] == "u_them"


def test_somebody_who_is_not_a_member_is_refused_not_added(tmp_path):
    idx = _owned(tmp_path)
    with pytest.raises(acts.ActRefused) as e:
        acts.set_membership(tmp_path, WS, email=NEW_MAIL, role="contributor", actor=OWNER,
                            index=idx, resolve_subject=lambda a: "u_stranger")
    assert e.value.status == 404 and "invite them" in str(e.value)
    assert m.is_member(tmp_path, WS, "u_stranger") is None


def test_the_last_owner_cannot_be_demoted_or_removed(tmp_path):
    idx = _owned(tmp_path)
    for role in ("reader", "remove"):
        with pytest.raises(acts.ActRefused) as e:
            acts.set_membership(tmp_path, WS, email=OWNER_MAIL, role=role, actor=OWNER, index=idx,
                                commit_fn=m.policy_commit)
        assert e.value.status == 409 and "last owner" in str(e.value)
    assert m.is_member(tmp_path, WS, OWNER) == "owner"


def test_a_role_that_is_not_one_names_the_four_answers(tmp_path):
    idx = _owned(tmp_path)
    with pytest.raises(acts.ActRefused) as e:
        acts.set_membership(tmp_path, WS, email=OWNER_MAIL, role="admin", actor=OWNER, index=idx)
    said = str(e.value)
    assert all(w in said for w in m.ROLE_WORDS) and "remove" in said


# ── the routes ───────────────────────────────────────────────────────────────────────────────────
def test_the_invite_route_is_owner_only_and_answers_what_it_did(tmp_path, monkeypatch):
    idx = _owned(tmp_path)
    published: list = []
    monkeypatch.setattr(publish_mod, "publish_invite",
                        lambda fact, **kw: (published.append(fact), True)[1])
    c = _client(tmp_path, index=idx)

    r = c.post("/api/workspace/invite",
               json={"slug": WS, "email": NEW_MAIL, "role": "contributor"},
               headers=_h(OWNER, OWNER_MAIL))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivery"] == "mailed" and body["role"] == "contributor"
    assert published and published[0]["email"] == NEW_MAIL

    assert c.post("/api/workspace/invite",
                  json={"slug": WS, "email": "someone@example.test", "role": "reader"},
                  headers=_h("u_stranger")).status_code == 403


def test_mint_then_preview_then_accept_through_the_three_routes(tmp_path, monkeypatch):
    """THE WALK THE FOUNDER TOOK, END TO END (Vexa-ai/vexa#1645 point 3). He minted through
    `POST /api/workspace/invite`, opened `/join?i=…` — which is `GET /api/workspace/invites/preview`
    — and read *"This invite link is not valid."*: 404, because the mint wrote a store the preview
    did not read. The three routes are asserted in one test, in that order, on the link the mint
    actually hands back, so "persisted somewhere else" cannot pass again."""
    idx = _owned(tmp_path)
    monkeypatch.setattr(publish_mod, "publish_invite", lambda fact, **kw: True)
    c = _client(tmp_path, index=idx)

    minted = c.post("/api/workspace/invite",
                    json={"slug": WS, "email": NEW_MAIL, "role": "contributor"},
                    headers=_h(OWNER, OWNER_MAIL))
    assert minted.status_code == 200, minted.text
    link = minted.json()["link"]
    assert "/join?i=" in link
    token = link.split("i=", 1)[1]

    # 2 — the join page's read, BEFORE any login: the same token the mint handed over resolves
    preview = c.get("/api/workspace/invites/preview", params={"token": token})
    assert preview.status_code == 200, preview.text
    assert preview.json()["workspace_id"] == WS
    assert preview.json()["valid"] is True
    assert preview.json()["role"] == "contributor"
    assert preview.json()["restricted_to"] == [NEW_MAIL]

    # 3 — redeemed by the address it names → membership, in both stores
    accepted = c.post("/api/workspace/invites/accept", json={"token": token},
                      headers=_h("u_jsmith", NEW_MAIL))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"workspace_id": WS, "role": "contributor", "already_member": False}
    assert m.is_member(tmp_path, WS, "u_jsmith") == "contributor"
    # and the link is spent: one address, one use
    assert c.get("/api/workspace/invites/preview", params={"token": token}).json()["valid"] is False


def test_a_fresh_mint_says_the_earlier_links_are_void_when_the_store_was_reset(tmp_path):
    """Point 4. The founder is holding links that can never be redeemed — the store was reset under
    them — and the one moment to tell him is when a new one is minted. Proven from the evidence that
    SURVIVED the loss: the workspace's own `policy: mint invite <id>` commits, which are still there
    after the file they described was deleted."""
    idx = _owned(tmp_path)
    ws = tmp_path / WS
    # the world as the founder found it: history says an invite was minted, the store has nothing
    (ws / "policy").mkdir(exist_ok=True)
    (ws / m.LEGACY_INVITES_FILE).write_text("[]\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "policy: mint invite 41cdb3b6a5841ffc (contributor) for " + WS)
    (ws / m.LEGACY_INVITES_FILE).unlink()
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", f"{WS}: policy/invites.json — removed")

    out = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                      ui_url="https://app.example.test", resolve_subject=lambda a: None)

    assert out["voided_earlier_links"] == ["41cdb3b6a5841ffc"]
    assert "void" in out["said"] and "this is the only one that works" in out["said"]


def test_a_mint_into_a_healthy_store_says_nothing_about_void_links(tmp_path):
    """The other half, and the reason the sentence is evidence-driven rather than always-on: a
    workspace that never lost an invite must not be told it did."""
    idx = _owned(tmp_path)
    first = acts.invite(tmp_path, WS, email=NEW_MAIL, role="reader", inviter=OWNER, index=idx,
                        ui_url="https://app.example.test", resolve_subject=lambda a: None)
    second = acts.invite(tmp_path, WS, email="other@example.com", role="reader", inviter=OWNER,
                         index=idx, ui_url="https://app.example.test",
                         resolve_subject=lambda a: None)
    assert first["voided_earlier_links"] == [] and second["voided_earlier_links"] == []
    assert "void" not in second["said"]


def test_the_membership_route_removes_by_address(tmp_path):
    idx = _owned(tmp_path)
    m.grant_membership(tmp_path, WS, "u_them", "contributor", added_by=OWNER, index=idx,
                       email=NEW_MAIL)
    c = _client(tmp_path, index=idx)
    r = c.post("/api/workspace/membership",
               json={"slug": WS, "email": NEW_MAIL, "role": "remove"},
               headers=_h(OWNER, OWNER_MAIL))
    assert r.status_code == 200, r.text
    assert r.json()["removed"] is True
    assert m.is_member(tmp_path, WS, "u_them") is None


def test_the_routes_refuse_the_private_tier_and_the_company_layer(tmp_path):
    _owned(tmp_path)
    c = _client(tmp_path, admins=OWNER)
    assert c.post("/api/workspace/invite", json={"slug": "_system", "email": NEW_MAIL,
                                                 "role": "reader"},
                  headers=_h(OWNER)).status_code == 403
    r = c.post("/api/workspace/invite", json={"slug": "_global", "email": NEW_MAIL,
                                              "role": "reader"}, headers=_h(OWNER))
    assert r.status_code == 409 and "POLICIES.md" in r.json()["detail"]


def test_the_route_body_is_a_named_model_so_the_verb_could_be_bound(tmp_path):
    """Not decoration: `core/agent/mcp.tools.v1.json` records three routes that cannot be bound at
    the assembled edge because they take a bare `body: dict`. A verb written today with one would
    have joined that list on the day it shipped."""
    from control_plane.api_shared import WorkspaceInviteBody, WorkspaceMembershipBody
    assert set(WorkspaceInviteBody.model_fields) == {"slug", "email", "role"}
    assert set(WorkspaceMembershipBody.model_fields) == {"slug", "email", "role"}
    assert WorkspaceInviteBody.model_fields["role"].default == "reader"


# ── the fact on the wire ─────────────────────────────────────────────────────────────────────────
def test_the_published_fact_carries_everything_the_mail_step_reads():
    fact = {"uid": OWNER, "email": NEW_MAIL, "workspace": WS, "workspace_name": "Pilot",
            "role": "contributor", "role_sentence": m.ROLE_SENTENCES["contributor"],
            "inviter": OWNER_MAIL, "link": "https://app.example.test/join?i=t",
            "expires_at": 1, "invite_id": "abc"}
    refs = publish_mod.invite_refs(fact)
    assert refs["uid"] == OWNER and refs["email"] == NEW_MAIL and refs["link"].endswith("=t")
    assert refs["expires_at"] == 1
    assert "invite_id" not in refs          # the id names the EVENT, it is not a ref
    assert publish_mod.invite_source_id(WS, "abc") == f"invite-{WS}-abc"


def test_two_deliberate_invites_to_one_person_are_two_events():
    """Keyed to the invite, never to the address: inviting the same person again after the first
    link expired is a second invitation and must be a second mail."""
    assert publish_mod.invite_source_id(WS, "one") != publish_mod.invite_source_id(WS, "two")


# ── the acts the buttons queue ───────────────────────────────────────────────────────────────────
def test_the_three_button_kinds_map_to_presets_that_exist():
    from control_plane import chat_intents
    repo = Path(__file__).resolve().parents[3]
    for kind in ("member_add", "member_role", "member_remove"):
        name = chat_intents.INTENT_PRESETS[kind]
        assert (repo / "behavior" / "asks" / f"{name}.md").is_file(), name


def test_a_membership_act_is_inline_and_visible_and_names_who_it_is_about():
    """Not a job (a question nobody is there to answer) and not silent (they pressed a label)."""
    from control_plane import chat_intents
    from shared.marks import act_label
    intent = {"kind": "member_role", "workspace": WS, "member": NEW_MAIL}
    assert not chat_intents.is_job(intent) and not chat_intents.is_silent(intent)
    prefix = chat_intents.act_prefix(intent)
    assert act_label(prefix) == f"Change role: {WS} · {NEW_MAIL}"
    assert act_label(chat_intents.act_prefix({"kind": "member_add", "workspace": WS})) \
        == f"Add a member: {WS}"


def test_the_asks_tell_the_agent_to_ask_confirm_then_call_the_verb():
    repo = Path(__file__).resolve().parents[3]
    add = (repo / "behavior" / "asks" / "member-add.md").read_text()
    assert "workspace_invite(slug=\"{{workspace}}\"" in add
    assert "Never guess an address" in add
    for name, verb in (("member-role", "workspace_membership"),
                       ("member-remove", "workspace_membership")):
        said = (repo / "behavior" / "asks" / f"{name}.md").read_text()
        assert verb in said and "{{member}}" in said
    assert "role=\"remove\"" in (repo / "behavior" / "asks" / "member-remove.md").read_text()
