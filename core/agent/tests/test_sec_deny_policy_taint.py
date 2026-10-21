"""Deny tests from the private 2026-10-17 security review: policy files, invites, the private-write
hold and person-only verbs (C3, C4, H3, H4, H7, H8).

Each test asserts that access is DENIED. A test that fails against today's code is marked
``xfail(strict=True)`` with the finding id, so it flips to a required pass once the protection
lands. Nothing here fixes product code.
"""
from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

import pytest

from control_plane import workspace_membership as m

from tests.test_r1_rooms import _h, world  # noqa: F401  (fixture: `team` owned by alice, bob contributor)


def _bob_as_watched_agent():
    """bob's own chat worker: a delegated identity in the human regime (a person watching)."""
    return {"X-User-Id": "bob", "x-user-regime": "human", "x-user-delegation-workspaces": "team",
            "x-user-delegation-target": "team"}


# ── C3: a contributor cannot rewrite `policy/` ──────────────────────────────────────────────────

POLICY_FILES = {
    "policy/members.json": lambda cur: json.dumps(
        [{**r, "role": "owner"} if r.get("subject") == "bob" else r for r in cur]),
    "policy/visibility.json": lambda cur: json.dumps({"visibility": "open"}),
    "policy/connections.json": lambda cur: json.dumps([{"workspace": "secret", "added_by": "bob"}]),
}


# Was xfail (C3/test 5); passes since s1a-policy (PRD 0.13.4 D5/D26).
@pytest.mark.parametrize("rel", sorted(POLICY_FILES))
def test_contributor_cannot_write_policy_files(world, rel):
    client, store, calls, spawned, root, _d = world
    before = (root / "team" / rel).read_text() if (root / "team" / rel).exists() else None
    content = POLICY_FILES[rel](m.read_members(root, "team"))
    r = client.put("/api/workspace/file", headers=_h("bob"),
                   json={"path": rel, "content": content, "slug": "team"})
    assert r.status_code == 403, f"{rel}: {r.status_code} {r.text[:200]}"
    after = (root / "team" / rel).read_text() if (root / "team" / rel).exists() else None
    assert after == before


def test_contributor_cannot_write_the_roster_under_another_spelling(world):
    """M3: on a case-insensitive filesystem `Policy/members.json` is the roster file."""
    client, store, calls, spawned, root, _d = world
    content = POLICY_FILES["policy/members.json"](m.read_members(root, "team"))
    for rel in ("Policy/members.json", "POLICY/members.json"):
        r = client.put("/api/workspace/file", headers=_h("bob"),
                       json={"path": rel, "content": content, "slug": "team"})
        assert r.status_code in (400, 403), f"{rel}: {r.status_code} {r.text[:200]}"
    assert m.is_member(root, "team", "bob") == "contributor"


# Was xfail (C3/test 5); passes since s1a-policy (PRD 0.13.4 D5/D26).
def test_contributors_watched_agent_cannot_write_policy_files(world):
    client, store, calls, spawned, root, _d = world
    content = POLICY_FILES["policy/members.json"](m.read_members(root, "team"))
    r = client.put("/api/workspace/file", headers=_bob_as_watched_agent(),
                   json={"path": "policy/members.json", "content": content, "slug": "team"})
    assert r.status_code == 403, f"{r.status_code} {r.text[:200]}"
    assert m.is_member(root, "team", "bob") == "contributor"


# Was xfail (C3/question 3); passes since s1a-policy (PRD 0.13.4 D5/D26).
def test_contributor_cannot_become_owner_through_the_page_route(world):
    client, store, calls, spawned, root, _d = world
    content = POLICY_FILES["policy/members.json"](m.read_members(root, "team"))
    client.put("/api/workspace/file", headers=_h("bob"),
               json={"path": "policy/members.json", "content": content, "slug": "team"})
    assert m.is_member(root, "team", "bob") == "contributor"
    with pytest.raises(m.MembershipError):
        m.require_role(root, "team", "bob", "owner")


# ── C3: a contributor cannot mint an owner invite ───────────────────────────────────────────────

# Was xfail (C3/test 6); passes since s1a-policy (PRD 0.13.4 D5/D26).
def test_contributor_cannot_mint_owner_invite(world):
    client, store, calls, spawned, root, _d = world
    r = client.post("/api/workspace/invites", headers=_h("bob"),
                    json={"workspace_id": "team", "role": "owner", "mode": "open"})
    assert r.status_code == 403, f"{r.status_code} {r.text[:200]}"


# Was xfail (C3/test 6); passes since s1a-policy (PRD 0.13.4 D5/D26).
@pytest.mark.parametrize("extra", [{"max_uses": 1_000_000}, {"expires_in_sec": 10 * 365 * 86400}])
def test_invite_uses_and_expiry_are_capped(world, extra):
    client, store, calls, spawned, root, _d = world
    r = client.post("/api/workspace/invites", headers=_h("alice"),
                    json={"workspace_id": "team", "role": "viewer", "mode": "open", **extra})
    assert r.status_code in (400, 422), f"{extra}: {r.status_code}"


# ── H3: person-only verbs need a person's session, not their watched agent ──────────────────────

# Was xfail (H3); required since 0.13.4 S4 (coordinator ruling 2026-10-17): every door that mints an
# invite or a join link takes only a person's session; an agent proposes an invitation card.
def test_watched_agent_cannot_mint_invite(world):
    client, store, calls, spawned, root, _d = world
    hdr = {**_bob_as_watched_agent(), "X-User-Id": "alice"}
    r = client.post("/api/workspace/invites", headers=hdr,
                    json={"workspace_id": "team", "role": "viewer", "mode": "open"})
    assert r.status_code == 403, f"{r.status_code} {r.text[:200]}"
    for path, body in (("/api/workspace/invite", {"slug": "team", "email": "x@acme.test", "role": "reader"}),
                       ("/api/workspace/invite-link", {"slug": "team", "role": "reader"})):
        r = client.post(path, headers=hdr, json=body)
        assert r.status_code == 403, f"{path} {r.status_code} {r.text[:200]}"
    # an API key is not a person's session either, whatever its scopes (S5, D13: identity states the class)
    key = {"X-User-Id": "alice", "x-user-scopes": "bot,tx,browser", "x-user-credential-class": "key"}
    assert client.post("/api/workspace/invites", headers=key,
                       json={"workspace_id": "team", "role": "viewer", "mode": "open"}).status_code == 403


# ── H4: accepting an invite never moves the person's write target ───────────────────────────────

# Was xfail (H4/test 15 (server half)); passes since s1a-policy (PRD 0.13.4 D5/D26).
def test_invite_accept_does_not_change_write_target(world):
    from control_plane import workspace_attach
    client, store, calls, spawned, root, _d = world
    tok = client.post("/api/workspace/invites", headers=_h("alice"),
                      json={"workspace_id": "team", "role": "viewer", "mode": "open"}).json()["token"]
    (root / "dave").mkdir(exist_ok=True)
    r = client.post("/api/workspace/invites/accept", headers=_h("dave"), json={"token": tok})
    assert r.status_code == 200, r.text
    assert workspace_attach.take_queued_profile(root, "dave") is None


# ── C4: the private-write mark sees every private read ──────────────────────────────────────────

PRIVATE_READ_TOOLS = ["crm_read", "crm_search", "get_meeting_transcript", "mail_inbox", "mail_read",
                      "people_followups", "customer_profile", "chat_read"]


@pytest.mark.parametrize("tool", PRIVATE_READ_TOOLS)
def test_private_read_marks_session_private(tool):
    from llm.private_writes import private_source
    assert private_source(f"mcp__vexa__{tool}") is not None, tool


def test_gmail_read_marks_session_private():
    """Control: the reads the list does name are marked."""
    from llm.private_writes import private_source
    assert private_source("mcp__vexa__gmail_read") == "mail"


def test_private_mark_survives_restart():
    from control_plane import private_taint
    private_taint.reset()
    private_taint.mark("agent-u-bob-s1", "mail")
    assert private_taint.sources("agent-u-bob-s1") == {"mail"}
    fresh = importlib.reload(private_taint)              # a new agent-api process
    try:
        assert fresh.sources("agent-u-bob-s1") == {"mail"}
    finally:
        fresh.reset()


def test_agent_message_from_marked_session_is_held():
    import inspect
    from control_plane.routers import agent_messages
    assert "private_taint" in inspect.getsource(agent_messages), \
        "the agent-message route has no private-write hold"


# ── H7: withheld content is not readable by another person's run ────────────────────────────────

def test_post_meeting_run_cannot_read_other_attendees_held_writes(tmp_path):
    """H7/test 9, widened by decision 3 (BL-2): a post-meeting run reaches NOTHING on another
    attendee's desk — not its held writes, not its pages — however the invite names them."""
    from control_plane.dispatch import build_mount_set
    from llm.private_writes import HELD_DIR
    from tests.test_meeting_room import _room, _seed, _settings
    root = tmp_path / "ws"
    _seed(root, "u_owner")
    desk = _seed(root, "u_bob")
    held = desk / HELD_DIR / "abc234.json"
    held.parent.mkdir(parents=True)
    held.write_text(json.dumps({"code": "abc234", "content": "d2l0aGhlbGQ="}))
    (desk / "notes.md").write_text("bob's own notes")
    stack = build_mount_set(_settings(tmp_path, root), "u_owner",
                            room=_room(desks=[("bob@x.test", "u_bob")]))
    for mt in stack:
        p = Path(mt["path"])
        assert p != desk and desk not in p.parents, f"{mt['slug']} reaches bob's desk"


# ── H8: a routine is armed only by a person who stood behind it ─────────────────────────────────

def test_routine_without_an_approval_record_is_not_stood_behind(tmp_path):
    from control_plane import routine_flows
    path = tmp_path / "alice" / "routines" / "daily.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ncron: '0 7 * * *'\n---\nbrief me")
    assert routine_flows._stood_behind(tmp_path, "alice", path, "deadbeef") is False


# ── H3: an inline-card decision is bound to a server-side pending item ──────────────────────────

# Was xfail (H3/test 13); passes since s5-cards (PRD 0.13.4 S5): the 7g card is a pending item bound
# to a server nonce and the content hash of the agent's card, decided once, never by a delegated caller.
def test_inline_card_decision_requires_browser_session(world):
    client, store, calls, spawned, root, _d = world
    # (the routes are exercised below; a missing one answers 404 and fails the 200/403/409 checks)
    from tests.test_s5_asks import _chat, _open
    sid = _chat(store)
    item = _open(client, sid).json()
    body = {"nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": "Create"}}
    url = f"/api/asks/{item['pending_id']}/answer"
    delegated = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "*"}
    assert client.post(url, json=body, headers=delegated).status_code == 403          # refused from a delegated caller
    assert client.post(url, json=body, headers=_h("alice")).status_code == 200        # bound to its item
    assert client.post(url, json=body, headers=_h("alice")).status_code == 409        # not replayable


# ── H5: the model's shell holds no provider credential ──────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="H5: model-provider keys stay in the harness subprocess env by "
                                       "design (ports.py:59-75, harness_subprocess_env) "
                                       "(security-validation-2026-10-17)")
@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"])
def test_model_shell_holds_no_provider_key(monkeypatch, var):
    from llm.ports import harness_subprocess_env
    monkeypatch.setenv(var, "test-placeholder-not-a-key")
    present = var in set(harness_subprocess_env())     # names only: never echo env values
    assert not present, f"{var} reaches the model's shell"


def test_model_shell_holds_no_delegation_bearer(monkeypatch):
    """Control: the toolbelt bearer and the data-plane secrets are stripped from the env."""
    from llm.ports import harness_subprocess_env
    for var in ("VEXA_MCP_DELEGATION_TOKEN", "REDIS_URL"):
        monkeypatch.setenv(var, "test-placeholder")
    names = set(harness_subprocess_env())               # names only: never echo env values
    assert not ({"VEXA_MCP_DELEGATION_TOKEN", "REDIS_URL"} & names)


# ── H10: git history tells a person's write from an agent's ─────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="H10: page-route commits use one fixed `vexa-terminal` identity for "
                                       "a person and their agent alike (workspaces.py:719-722) "
                                       "(security-validation-2026-10-17)")
def test_agent_page_write_is_attributed_differently_from_the_persons(world):
    import subprocess
    client, store, calls, spawned, root, _d = world
    agent = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "team",
             "x-user-delegation-target": "team"}
    assert client.put("/api/workspace/file", headers=_h("alice"),
                      json={"path": "a.md", "content": "by alice\n", "slug": "team"}).status_code == 200
    assert client.put("/api/workspace/file", headers=agent,
                      json={"path": "b.md", "content": "by her agent\n", "slug": "team"}).status_code == 200
    log = subprocess.run(["git", "-C", str(root / "team"), "log", "-2", "--format=%an <%ae>|%B%x00"],
                         capture_output=True, text=True, check=True).stdout
    entries = [e.strip() for e in log.split("\x00") if e.strip()]
    assert len(entries) == 2, entries
    (agent_author, agent_body), (person_author, _pb) = (e.split("|", 1) for e in entries)
    assert agent_author != person_author or "on-behalf-of" in agent_body.lower(), entries


# ── deny test 37 (PRD 0.13.4 D38): a workspace policy line cannot lower a floor ────────────────
@pytest.mark.parametrize("action", ["guest.join", "nudge.free_text", "card.destructive_first_tap"])
def test_a_workspace_rule_cannot_lower_a_floor(action):
    from control_plane import policy_decide as pd
    ask = pd.Ask(principal="alice", action=action, workspace="team", role="owner", credential=pd.SESSION)
    out = pd.decide(ask, workspace_rules=[{"id": "ws.lower", "effect": "allow", "action": action}])
    assert out.decision == pd.DENY and out.rule.startswith("floor."), out


@pytest.mark.parametrize("rules", [[{"id": "g", "effect": "allow", "action": "guest.*"}],
                                   [{"id": "n", "effect": "allow", "action": "nudge.*"}],
                                   [{"id": "d", "effect": "allow", "action": "*"}]])
def test_a_rules_proposal_that_lowers_a_floor_is_refused(rules):
    from control_plane import policy_lines, policy_proposals
    assert policy_lines.lowered_floors(rules=rules)
    with pytest.raises(ValueError, match="floor"):
        policy_proposals.normalize("rules", rules)
    assert policy_lines.lowered_floors(lines={"guest_access": "allowed"}) == ["guest_access"]
    assert policy_lines.lowered_floors(lines={"nudge_body": "template_only"}) == []
