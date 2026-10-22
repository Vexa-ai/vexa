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

import fakeredis
import pytest

from control_plane import workspace_membership as m
from workspaces.shared import gitexec

from tests.test_r1_rooms import _h, world  # noqa: F401  (fixture: `team` owned by alice, bob contributor)
from tests.test_workspace_store_service import (  # noqa: F401  (fixture: an s3 store; `team` read by bob)
    AUTHOR, _worker_commit, headers, record, stack)


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

# Was xfail (H5/W1); passes since the model proxy (0.13.4 car 37): no worker is given a provider key,
# and none reaches a harness subprocess even if one sat in the worker's environment.
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


# ── an ask that names no credential is never a person's session (gate Low: credential default) ──

def test_an_ask_that_names_no_credential_never_passes_a_session_only_rule():
    from control_plane import policy_decide as pd
    out = pd.decide(pd.Ask(principal="alice", action="policy.change", workspace="team", role="owner"),
                    workspace_rules=[])
    assert out.decision == pd.DENY, out
    # and a floor that denies keys denies it too (``floor.policy-change-needs-a-session``)
    assert out.rule.startswith("floor."), out


def test_an_ask_that_names_the_session_still_passes():
    """The control: the owner's own session, named, changes policy."""
    from control_plane import policy_decide as pd
    out = pd.decide(pd.Ask(principal="alice", action="policy.change", workspace="team", role="owner",
                           credential=pd.SESSION), workspace_rules=[])
    assert out.allowed, out


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


# ── M2: a workspace push is held to the private-write rule on the server; M3: reserved push paths ─

CHAT_UNIT = "agent-17-chat-c1"


@pytest.fixture
def marked(monkeypatch):
    """A taint store on fakeredis; ``marked(unit)`` records that the unit's session read 17's mail."""
    from control_plane import private_taint
    from shared import taint as taint_mod

    store = taint_mod.TaintStore(fakeredis.FakeRedis(decode_responses=True))
    taint_mod.configure(store)
    private_taint.reset()
    yield lambda unit: store.mark(unit, ["mail"])
    taint_mod.configure(None)
    private_taint.reset()


def _fetch(stack, slug: str, unit: str = CHAT_UNIT) -> bytes:
    return stack.client.get("/api/workspace-store/fetch", params={"path": f"{stack.cache}/{slug}"},
                            headers=headers(unit=unit)).content


def _push(stack, slug: str, base: str, data: bytes, unit: str = CHAT_UNIT):
    return stack.client.post("/api/workspace-store/push", params={"path": f"{stack.cache}/{slug}", "base": base},
                             content=data, headers=headers(unit=unit))


def test_a_marked_sessions_push_into_a_workspace_others_read_is_held(stack, tmp_path, marked):
    """M2: the worker's hold sees only the uncommitted change; a harness that commits by itself
    reaches the store with the private text in a commit. The store route holds it."""
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "team"),
                                   {"notes/bank.md": "Invoice 4471-2210 from Dana Whitfield"})
    r = _push(stack, "team", base, data)
    assert r.status_code == 409, r.text[:300]
    body = r.json()["detail"]
    assert body["refused"] == "private_sources_to_shared" and body["paths"] == ["notes/bank.md"]
    assert body["readers"] == ["bob@example.com"] and body["sources"] == ["mail"]
    assert stack.run.store.latest("team").gen == 1, "nothing landed"
    assert gitexec.run_git(str(stack.cache / "team"), "rev-parse", "HEAD", check=True).stdout.strip() == base


def test_a_push_that_adds_then_removes_the_private_text_is_still_held(stack, tmp_path, marked):
    """History is read by the others too: every commit in the range is a write, not only the tip."""
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, _, _ = _worker_commit(tmp_path, _fetch(stack, "team"), {"notes/bank.md": "Dana Whitfield owes 900 EUR"})
    w = tmp_path / "worker"
    gitexec.run_git(str(w), "rm", "-q", "notes/bank.md", check=True)
    gitexec.run_git(str(w), *AUTHOR, "commit", "-q", "-m", "tidy", check=True)
    out = tmp_path / "two.bundle"
    gitexec.run_git(str(w), "bundle", "create", "-q", str(out), "refs/heads/main", f"^{base}", check=True)
    r = _push(stack, "team", base, out.read_bytes())
    assert r.status_code == 409 and r.json()["detail"]["paths"] == ["notes/bank.md"], r.text[:300]
    assert stack.run.store.latest("team").gen == 1


def test_a_push_of_many_files_reads_their_versions_in_one_git_process(stack, tmp_path, marked, monkeypatch):
    """Security review 16.1: the hold runs under the workspace lock, so its git processes grow with
    the commits pushed, never with the files in them."""
    from workspaces.shared import gitexec as gx
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    files = {f"notes/n{i}.md": f"line {i}" for i in range(40)}
    files["notes/bank.md"] = "Invoice 4471-2210 from Dana Whitfield"
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "team"), files)
    reads = []
    real = gx.run_git

    def counting(cwd, *args, **kw):
        if args and args[0] == "cat-file":
            reads.append(args)
        return real(cwd, *args, **kw)
    monkeypatch.setattr(gx, "run_git", counting)
    r = _push(stack, "team", base, data)
    assert r.status_code == 409 and "notes/bank.md" in r.json()["detail"]["paths"], r.text[:300]
    assert len(reads) == 1 and reads[0][:2] == ("cat-file", "--batch"), reads[:3]


def test_an_unmarked_session_or_the_persons_own_desk_pushes_as_before(stack, tmp_path, marked):
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit="agent-17-chat-clean")
    base, new, data = _worker_commit(tmp_path, _fetch(stack, "team", unit="agent-17-chat-clean"),
                                     {"notes/plan.md": "Ship on Friday"})
    r = _push(stack, "team", base, data, unit="agent-17-chat-clean")
    assert r.status_code == 200 and r.json()["head"] == new, r.text[:300]
    record(stack, [{"path": f"{stack.cache}/17", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    desk = tmp_path / "desk"
    desk.mkdir()
    base, new, data = _worker_commit(desk, _fetch(stack, "17"), {"notes/bank.md": "Dana Whitfield owes 900 EUR"})
    r = _push(stack, "17", base, data)
    assert r.status_code == 200 and r.json()["head"] == new, "nobody else reads the person's desk"


# ── M2-b: the push hold's audience is the authoritative roster, never the tree's mirror ─────────

def _registry(stack, slug: str, rows: list) -> None:
    """The workspace's roster in the policy store (what every access decision reads)."""
    m.store_put(stack.cache, slug, "members", rows)


def test_a_push_into_a_workspace_whose_mirror_is_absent_is_held_on_the_registry_roster(stack, tmp_path, marked):
    """M2-b: ``deal`` has no ``policy/members.json`` in its tree, and bob is in its registry roster."""
    _registry(stack, "deal", [{"subject": "17", "role": "owner"},
                              {"subject": "bob", "email": "bob@example.com", "role": "viewer"}])
    record(stack, [{"path": f"{stack.cache}/deal", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "deal"),
                                   {"notes/bank.md": "Invoice 4471-2210 from Dana Whitfield"})
    r = _push(stack, "deal", base, data)
    assert r.status_code == 409, r.text[:300]
    assert r.json()["detail"]["readers"] == ["bob@example.com"]
    assert stack.run.store.latest("deal").gen == 1, "nothing landed"


def test_a_push_hold_names_the_registry_readers_when_the_mirror_is_stale(stack, tmp_path, marked):
    """M2-b: the mirror still lists bob, whom the owner removed, and does not list carol, added since."""
    _registry(stack, "team", [{"subject": "17", "role": "owner"},
                              {"subject": "carol", "email": "carol@example.com", "role": "viewer"}])
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "team"),
                                   {"notes/bank.md": "Invoice 4471-2210 from Dana Whitfield"})
    r = _push(stack, "team", base, data)
    assert r.status_code == 409, r.text[:300]
    assert r.json()["detail"]["readers"] == ["carol@example.com"]


def test_a_push_hold_releases_when_the_registry_says_only_the_person_reads(stack, tmp_path, marked):
    """M2-b, the other direction: the mirror lists bob, the registry says the workspace is 17's alone."""
    _registry(stack, "team", [{"subject": "17", "role": "owner"}])
    record(stack, [{"path": f"{stack.cache}/team", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, new, data = _worker_commit(tmp_path, _fetch(stack, "team"), {"notes/bank.md": "Dana Whitfield"})
    r = _push(stack, "team", base, data)
    assert r.status_code == 200 and r.json()["head"] == new, r.text[:300]


class _DownStore:
    """A policy store that does not answer."""

    def __getattr__(self, name):
        def fail(*a, **kw):
            raise ConnectionError("registry down")
        return fail


def test_a_marked_push_does_not_land_when_the_registry_cannot_be_read(stack, tmp_path, marked):
    """M2-b: an unknown audience is never "nobody else" — the push is refused, nothing lands."""
    record(stack, [{"path": f"{stack.cache}/deal", "write": True}], unit=CHAT_UNIT)
    marked(CHAT_UNIT)
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "deal"), {"notes/bank.md": "Dana Whitfield"})
    m.set_policy_store(_DownStore())
    try:
        r = _push(stack, "deal", base, data)
    finally:
        m.set_policy_store(None)
    assert r.status_code == 503 and r.json()["detail"]["refused"] == "policy_unreadable", r.text[:300]
    assert stack.run.store.latest("deal").gen == 1, "nothing landed"


def test_an_unmarked_push_lands_without_a_roster_read(stack, tmp_path, marked):
    """The roster is read only for a session that read something private."""
    record(stack, [{"path": f"{stack.cache}/deal", "write": True}], unit="agent-17-chat-clean")
    base, new, data = _worker_commit(tmp_path, _fetch(stack, "deal", unit="agent-17-chat-clean"),
                                     {"notes/plan.md": "Ship on Friday"})
    m.set_policy_store(_DownStore())
    try:
        r = _push(stack, "deal", base, data, unit="agent-17-chat-clean")
    finally:
        m.set_policy_store(None)
    assert r.status_code == 200 and r.json()["head"] == new, r.text[:300]


def test_a_page_write_is_held_on_the_registry_roster_when_the_tree_mirror_is_gone(world):
    """M2-b at the page route: the mirror is deleted from ``team``'s tree; alice and bob still read
    it, so a write whose mark cannot be checked is held."""
    from tests.test_int_s1_seams import _principal
    client, store, calls, spawned, root, _d = world
    (root / "team" / "policy" / "members.json").unlink()
    r = client.put("/api/workspace/file", headers=_principal(unit=""),
                   json={"slug": "team", "path": "held.md", "content": "x"})
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "private_sources_to_shared", r.text[:300]
    assert sorted(r.json()["detail"]["readers"]) == ["alice", "bob"]
    assert not (root / "team" / "held.md").exists()


def test_the_page_routes_audience_is_the_registry_roster_not_the_mirror(tmp_path):
    """M2-b at the shared reader: a workspace with no mirror still has its registry's readers, a
    stale mirror changes nobody's audience, and a tree outside the store's top level has none."""
    from control_plane import private_taint
    root = tmp_path / "ws"
    (root / "deal").mkdir(parents=True)
    (root / "team" / "policy").mkdir(parents=True)
    (root / "team" / "policy" / "members.json").write_text(
        '[{"subject": "17", "role": "owner"}, {"subject": "bob", "role": "viewer"}]')
    m.store_put(root, "deal", "members", [{"subject": "17", "role": "owner"},
                                          {"subject": "bob", "email": "bob@example.com", "role": "viewer"}])
    m.store_put(root, "team", "members", [{"subject": "17", "role": "owner"}])
    assert private_taint.other_readers(root, root / "deal", "17") == ["bob@example.com"]
    assert private_taint.other_readers(root, root / "team", "17") == []
    assert private_taint.other_readers(root, root / ".attached" / "17" / "deal", "17") == []


@pytest.mark.parametrize("rel", [".claude/settings.json", ".Claude/settings.json", "Policy/members.json",
                                 ".VEXA/state.json", "policy/réglage.json"])
def test_a_push_touching_a_reserved_path_in_any_case_is_refused(stack, tmp_path, rel):
    """M3 residual: the push prefixes are compared casefolded, include ``.claude/``, and read a
    non-ASCII path as itself (git quotes it in a line-separated listing)."""
    record(stack, [{"path": f"{stack.cache}/17", "write": True}])
    base, _, data = _worker_commit(tmp_path, _fetch(stack, "17", unit="agent-u-1"), {rel: "{}"})
    r = _push(stack, "17", base, data, unit="agent-u-1")
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "reserved_path", r.text[:300]
    assert stack.run.store.latest("17").gen == 1


# ── Low (gate 0.13.4 final, car 43): a removed member pushes nothing more, at once ──────────────

BOB_UNIT = "agent-bob-chat-c9"


def _bob_on_team(stack, role="contributor"):
    from control_plane import workspace_store_service as svc
    root = stack.cache
    m.store_put(root, "team", "members", [{"subject": "17", "role": "owner"},
                                          {"subject": "bob", "role": role}])
    svc.record_unit_mounts(None, BOB_UNIT, "bob", {"VEXA_MOUNTS": json.dumps(
        [{"slug": "team", "path": f"{root}/team", "role": "shared", "write": True}])})


def _bob(stack, verb, **kw):
    path = f"{stack.cache}/team"
    if verb == "fetch":
        return stack.client.get("/api/workspace-store/fetch", params={"path": path},
                                headers=headers(unit=BOB_UNIT, subject="bob"))
    return stack.client.post("/api/workspace-store/push", params={"path": path, "base": kw["base"]},
                             content=kw["data"], headers=headers(unit=BOB_UNIT, subject="bob"))


def test_a_member_removed_after_the_dispatch_pushes_and_fetches_nothing_more(stack, tmp_path):
    _bob_on_team(stack)
    got = _bob(stack, "fetch")
    assert got.status_code == 200
    base, _new, data = _worker_commit(tmp_path, got.content, {"notes/bob.md": "after I left\n"})
    gen = stack.run.store.latest("team").gen
    m.store_put(stack.cache, "team", "members", [{"subject": "17", "role": "owner"}])   # bob removed
    r = _bob(stack, "push", base=base, data=data)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_a_member", r.text[:300]
    assert stack.run.store.latest("team").gen == gen
    assert _bob(stack, "fetch").status_code == 403


def test_a_member_made_a_viewer_after_the_dispatch_pushes_nothing_more(stack, tmp_path):
    _bob_on_team(stack)
    base, _new, data = _worker_commit(tmp_path, _bob(stack, "fetch").content, {"notes/bob.md": "x\n"})
    gen = stack.run.store.latest("team").gen
    m.store_put(stack.cache, "team", "members", [{"subject": "17", "role": "owner"},
                                                 {"subject": "bob", "role": "viewer"}])
    r = _bob(stack, "push", base=base, data=data)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "read_only", r.text[:300]
    assert stack.run.store.latest("team").gen == gen
    assert _bob(stack, "fetch").status_code == 200                      # a viewer still reads


def test_a_member_still_on_the_roster_pushes(stack, tmp_path):
    """Control: nothing changed after the dispatch; bob's push lands."""
    _bob_on_team(stack)
    base, _new, data = _worker_commit(tmp_path, _bob(stack, "fetch").content, {"notes/bob.md": "x\n"})
    gen = stack.run.store.latest("team").gen
    r = _bob(stack, "push", base=base, data=data)
    assert r.status_code == 200, r.text[:300]
    assert stack.run.store.latest("team").gen == gen + 1


# ── M2-b on the volume backend: the worker's commit hold counts agent-api's read of the roster ────

def _volume_turn(tmp_path, env_readers, *, tree_members=None):
    """A worker started from a dispatch env (``Guard.from_env``) whose turn reads mail, then writes a
    page into ``meetup``. Returns (the turn's events, meetup's commit count)."""
    from llm.ports import run_harness_turn
    from llm.private_writes import Guard
    from tests.test_private_writes_commit import AUTHOR as WAUTHOR, OWNER, _Harness, _log, _repo
    desk = _repo(tmp_path / OWNER)
    shared = _repo(tmp_path / "meetup", members=tree_members)
    env = {"VEXA_OWNER": OWNER}
    if env_readers is not None:
        env["VEXA_MOUNT_READERS"] = json.dumps(env_readers(desk, shared))
    guard = Guard.from_env(env, [{"path": str(desk)}, {"path": str(shared)}])
    h = _Harness("mcp__vexa__gmail_search", [(shared / "notes/bank.md", "Invoice 4471-2210")])
    events = list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                   author=WAUTHOR))
    return [e for e in events if e["type"] == "write-withheld"], _log(shared), guard, shared


def test_the_worker_holds_on_the_registry_readers_when_the_tree_copy_is_missing(tmp_path):
    held, commits, *_ = _volume_turn(tmp_path, lambda d, s: {str(d): [], str(s): ["bob@example.com"]})
    assert held and held[0]["readers"] == ["bob@example.com"], held
    assert commits == 1, "nothing committed into meetup"


def test_the_worker_counts_the_registry_readers_not_a_stale_tree_copy(tmp_path):
    stale = [{"subject": "7", "role": "owner"}, {"subject": "bob", "email": "bob@example.com", "role": "viewer"}]
    held, commits, *_ = _volume_turn(tmp_path, lambda d, s: {str(d): [], str(s): ["carol@example.com"]},
                                     tree_members=stale)
    assert held and held[0]["readers"] == ["carol@example.com"], held
    assert commits == 1


def test_the_worker_commits_when_the_registry_says_only_the_person_reads(tmp_path):
    stale = [{"subject": "7", "role": "owner"}, {"subject": "bob", "email": "bob@example.com", "role": "viewer"}]
    held, commits, *_ = _volume_turn(tmp_path, lambda d, s: {str(d): [], str(s): []}, tree_members=stale)
    assert held == [] and commits == 2


@pytest.mark.parametrize("readers", [lambda d, s: {str(d): [], str(s): None}, lambda d, s: {str(d): []}, None],
                         ids=["registry-unreadable", "mount-not-answered", "no-audience"])
def test_the_worker_holds_when_the_registry_could_not_be_read(tmp_path, readers):
    """Fail closed: a mount agent-api could not read, did not answer for, or a worker told nothing."""
    held, commits, *_ = _volume_turn(tmp_path, readers)
    assert held and commits == 1, "the change into meetup is held, nothing committed"
    from llm.private_writes import READERS_UNKNOWN
    assert held[0]["readers"] == [READERS_UNKNOWN], held


def test_each_turn_brings_a_fresh_audience(tmp_path):
    """A member added after the worker started: the next turn's entry carries them."""
    *_, guard, shared = _volume_turn(tmp_path, lambda d, s: {str(d): [], str(s): []})
    assert guard.readers(shared) == []
    guard.set_audience({str(shared): ["dana@example.com"]})
    assert guard.readers(shared) == ["dana@example.com"]


# ── M2-b freshness (gate 0.13.4 final, car 43): the audience is as fresh as the commit ──────────

def _live_turn(tmp_path, during):
    """A worker of unit ``agent-7-chat-live`` (its own Redis, ``configure_worker``) mounts ``meetup``,
    a shared workspace only 7 reads at the dispatch. Its turn reads mail, writes a page into meetup,
    and ``during(root)`` runs WHILE the turn runs (a roster change). Returns (held, meetup commits,
    the turn's events)."""
    import fakeredis
    from llm import private_writes
    from llm.ports import run_harness_turn
    from llm.private_writes import Guard
    from tests.test_private_writes_commit import AUTHOR as WAUTHOR, OWNER, _Harness, _log, _repo
    root, unit = tmp_path, "agent-7-chat-live"
    desk, shared = _repo(root / OWNER), _repo(root / "meetup")
    m.store_put(root, "meetup", "members", [{"subject": OWNER, "role": "owner"},
                                            {"subject": "8", "role": "contributor"}])
    fake = fakeredis.FakeRedis(decode_responses=True)
    stamp = {str(desk): [], str(shared): []}            # the dispatch's read: only 7 (and 8, below)
    mounts = [{"path": str(desk), "role": "private", "write": True},
              {"path": str(shared), "role": "shared", "write": True}]
    try:
        from control_plane import mount_audience
    except ImportError:                                 # a tree without the live audience
        mount_audience = None
    if mount_audience is not None:
        mount_audience.publish(fake, root, unit, OWNER, mounts, stamp)
        m.set_roster_listener(lambda r_, ws: mount_audience.refresh(fake, r_, ws))
    private_writes.configure_worker(fake, unit)
    try:
        guard = Guard.from_env({"VEXA_OWNER": OWNER, "VEXA_MOUNT_READERS": json.dumps(stamp)},
                               [{"path": str(desk)}, {"path": str(shared)}])

        class _MidTurn(_Harness):
            def run_turn(self, work, prompt, **kw):
                for ev in super().run_turn(work, prompt, **kw):
                    if ev.get("type") == "done":
                        during(root)
                    yield ev
        h = _MidTurn("mcp__vexa__gmail_search", [(shared / "notes/bank.md", "Invoice 4471-2210")])
        events = list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                       author=WAUTHOR))
    finally:
        private_writes.configure_worker(None, "")
        if hasattr(m, "set_roster_listener"):
            m.set_roster_listener(None)
    return [e for e in events if e["type"] == "write-withheld"], _log(shared), events


def test_a_member_added_while_the_turn_runs_holds_its_commit(tmp_path):
    """The dispatch saw only the person; dana joins meetup mid-turn: the commit is held on dana."""
    def add_dana(root):
        rows = m.read_members(root, "meetup")
        m.write_policy(root, "meetup", "members",
                       rows + [{"subject": "dana", "email": "dana@example.com", "role": "viewer"}])
    held, commits, _ev = _live_turn(tmp_path, add_dana)
    assert held and "dana@example.com" in held[0]["readers"], held
    assert commits == 1, "nothing committed into meetup"


def test_with_no_roster_change_the_turn_commits_as_before(tmp_path):
    """Control: nobody joined; the dispatch's audience still holds (only the person reads)."""
    held, commits, _ev = _live_turn(tmp_path, lambda root: None)
    assert held == [] and commits == 2


def test_a_member_removed_while_the_turn_runs_commits_nothing_there(tmp_path, monkeypatch):
    """The unit's subject leaves meetup mid-turn: the turn's change there is put back, not committed
    (the volume backend's half of 'a removed member pushes nothing more')."""
    from tests.test_private_writes_commit import OWNER
    def remove_owner(root):
        m.write_policy(root, "meetup", "members", [{"subject": "8", "role": "owner"}])
    held, commits, events = _live_turn(tmp_path, remove_owner)
    assert commits == 1
    refused = [e for e in events if e.get("type") == "write-refused"]
    assert refused and refused[0]["paths"] == ["notes/bank.md"], events
    assert not (tmp_path / "meetup" / "notes" / "bank.md").exists()
    assert OWNER not in [r["subject"] for r in m.read_members(tmp_path, "meetup")]


def test_an_audience_that_cannot_be_reread_holds_every_mount(tmp_path, monkeypatch):
    """Fail closed: the roster change could not be re-read for the unit: unknown, held."""
    from llm.private_writes import READERS_UNKNOWN

    def add_with_store_down(root):
        rows = m.read_members(root, "meetup")
        m.store_put(root, "meetup", "members", rows + [{"subject": "dana", "role": "viewer"}])
        m.set_policy_store(_DownStore())
        try:
            m._roster_changed(root, "meetup") if hasattr(m, "_roster_changed") else None
        finally:
            m.set_policy_store(None)
    held, commits, _ev = _live_turn(tmp_path, add_with_store_down)
    assert held and held[0]["readers"] == [READERS_UNKNOWN] and commits == 1, held


def test_the_dispatch_stamps_each_mounts_readers_from_the_registry(tmp_path):
    from control_plane.dispatch import build_unit_env
    from shared.config import load_settings
    inv = {"identity": {"subject": "u_1", "launcher": "user:u_1"}, "runner": "openai-agent",
           "workspaces": [{"id": "u_1", "mode": "rw"}], "trigger": "message",
           "start": {"entrypoint": {"inline": "hi"}}}
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), inv, unit_id="unit-1", token="tok")
    readers = json.loads(env["VEXA_MOUNT_READERS"])
    mounts = json.loads(env["VEXA_MOUNTS"])
    desk = next(mt["path"] for mt in mounts if mt.get("desk"))
    assert set(readers) == {str(Path(mt["path"])) for mt in mounts}
    assert readers[str(Path(desk))] == []
    m.set_policy_store(_DownStore())
    try:
        env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), inv, unit_id="unit-1", token="tok")
    finally:
        m.set_policy_store(None)
    assert json.loads(env["VEXA_MOUNT_READERS"])[str(Path(desk))] is None
