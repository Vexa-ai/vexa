"""ADR-0048 decisions 4 and 5 — the two gates, the leak rule, and when the agent speaks."""
from __future__ import annotations

import pytest

from control_plane import session_access as sa


# ── decision 5: when the agent speaks ─────────────────────────────────────────────────────────
def test_one_person_the_agent_answers_every_message():
    assert sa.agent_speaks("what is next?", humans=1, policy="mentioned")


def test_two_people_the_agent_answers_only_when_mentioned():
    assert not sa.agent_speaks("I'll take the migration", humans=2, policy="mentioned")
    assert sa.agent_speaks("@vexa what did we agree?", humans=2, policy="mentioned")
    assert sa.agent_speaks("Hey @Vexa, summarise", humans=3, policy="mentioned")
    assert sa.agent_speaks("@scout check the deck", humans=2, policy="mentioned", agent_names=["scout"])
    assert not sa.agent_speaks("email me at a@vexa.ai", humans=2, policy="mentioned")


def test_a_direct_ask_counts_as_a_mention():
    assert sa.agent_speaks("vexa, what is the date?", humans=2, policy="mentioned")


def test_policy_always_answers_every_message():
    assert sa.agent_speaks("I'll take the migration", humans=4, policy="always")


def test_policy_is_read_from_the_channel_then_global(tmp_path):
    g = tmp_path / "_global"
    g.mkdir()
    (g / "POLICIES.md").write_text("---\nkind: policies\nsession_agent_speaks: always\n---\n# x\n")
    assert sa.speaks_policy(g) == "always"
    ch = tmp_path / "team"
    ch.mkdir()
    (ch / "POLICIES.md").write_text("---\nsession_agent_speaks: mentioned\n---\n")
    assert sa.speaks_policy(g, channel_root=ch) == "mentioned"
    assert sa.speaks_policy(tmp_path / "absent") == sa.DEFAULT_SPEAKS
    (g / "POLICIES.md").write_text("---\nsession_agent_speaks: sometimes\n---\n")
    assert sa.speaks_policy(g) == sa.DEFAULT_SPEAKS          # unknown answer → the default


# ── decision 4: the leak rule ─────────────────────────────────────────────────────────────────
def _members(table):
    return lambda ws, who: who in table.get(ws, ())


def test_a_private_session_keeps_the_speakers_own_reach():
    scope = sa.shared_scope("alice", others=[], candidates=["alice", "team", "_system", "_global"],
                            is_member=_members({}))
    assert scope is None                         # one person: no narrowing at all


def test_a_shared_session_uses_only_what_every_participant_can_read():
    scope = sa.shared_scope(
        "alice", others=["bob"],
        candidates=["alice", "team", "alice-secret", "_system", "_global"],
        is_member=_members({"team": {"alice", "bob"}, "alice-secret": {"alice"}}))
    assert scope.allowed == ["team", "_global"]
    assert scope.withheld == ["alice", "alice-secret", "_system"]


def test_a_participant_who_cannot_read_narrows_everyone():
    scope = sa.shared_scope(
        "alice", others=["bob", "carol"], candidates=["team"],
        is_member=_members({"team": {"alice", "bob"}}))
    assert scope.allowed == [] and scope.withheld == ["team"]


def test_the_leak_preamble_tells_the_agent_to_offer_the_private_chat():
    scope = sa.shared_scope("alice", others=["bob"], candidates=["alice-secret", "team"],
                            is_member=_members({"team": {"alice", "bob"}, "alice-secret": {"alice"}}))
    text = sa.leak_preamble(scope, names={"alice-secret": "Alice's pricing notes"}, people=2)
    assert "Alice's pricing notes" not in text and "some workspaces are withheld" in text.lower()
    assert "can't use it here" in text and "private chat" in text


def test_an_explicit_share_is_one_item_quoted_for_this_turn():
    text = sa.shared_items_block([{"label": "pricing.md", "text": "Price: 10"}], speaker="Alice")
    assert "Alice shared" in text and "Price: 10" in text
    assert sa.shared_items_block([], speaker="Alice") == ""


def test_the_leak_scenario_end_to_end():
    """A asks in a shared chat about a page only A can read → the turn cannot reach that page,
    and the agent is told to say so and offer A's private chat."""
    scope = sa.shared_scope("alice", others=["bob"], candidates=["alice", "team", "_global"],
                            is_member=_members({"team": {"alice", "bob"}}))
    mounts = [{"slug": "_global"}, {"slug": "alice", "primary": True}, {"slug": "team"},
              {"slug": "_system", "role": "system"}]
    kept = sa.narrow_mounts(mounts, scope)
    assert [m["slug"] for m in kept] == ["_global", "team"]
    pre = sa.leak_preamble(scope, names={"alice": "Alice's desk"}, people=2)
    assert "Alice's desk" not in pre and "withheld" in pre and "private chat" in pre


@pytest.mark.parametrize("bad", [None, "", "  "])
def test_no_policy_reads_as_the_default(bad):
    assert sa.normalise_speaks(bad) == sa.DEFAULT_SPEAKS


# ── the leak rule reaches the worker: mounts and the tools' ceiling ──────────────────────────
def test_a_shared_turn_mounts_only_the_intersection_and_its_tools_reach_no_further(tmp_path):
    import json

    from control_plane.dispatch import SHARED_SCRATCH_ENV, build_unit_env
    from shared import delegation
    from shared.config import load_settings

    secret = "test-delegation-secret"
    settings = load_settings(workspaces_dir=str(tmp_path), mcp_url="https://rig.example/mcp",
                             mcp_delegation_secret=secret)
    inv = {"identity": {"subject": "58", "launcher": "user:58"}, "runner": "claude-code",
           "workspaces": [{"id": "58", "mode": "rw"}], "trigger": "message",
           "context": {"kind": "none"}, "start": {"entrypoint": {"inline": "hi"}}}
    private = build_unit_env(settings, inv, unit_id="u1", token="t")
    assert any(m["slug"] == "_system" for m in json.loads(private["VEXA_MOUNTS"]))
    scope = sa.SharedScope(allowed=["_global"], withheld=["58", "_system"])
    env = build_unit_env(settings, inv, unit_id="u1", token="t", shared_scope=scope)
    slugs = [m["slug"] for m in json.loads(env["VEXA_MOUNTS"])]
    assert "58" not in slugs and "_system" not in slugs
    assert env[SHARED_SCRATCH_ENV] == "1"                  # the worker works in a per-session scratch
    assert env["VEXA_WORKSPACE_PATH"].startswith(str(tmp_path))   # …and the runtime still admits the cwd
    assert SHARED_SCRATCH_ENV not in private
    claims = delegation.verify_delegation(secret, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["scope"]["workspaces"] == ["_global"]
    assert not delegation.scope_allows_workspace(claims, "58")
    # S3: the token says other people read this turn, so every service refuses a person-wide read
    assert claims["scope"]["shared"] is True
    assert "shared" not in delegation.verify_delegation(secret, private["VEXA_MCP_DELEGATION_TOKEN"])["scope"]


# ── S10: one front-matter reader; an unknown answer is said, not silently swallowed ──────────
def test_policy_reads_through_the_one_front_matter_reader(tmp_path):
    """The same reader the workspace routes use for POLICIES.md: quoted values and comment lines."""
    g = tmp_path / "_global"
    g.mkdir()
    (g / "POLICIES.md").write_text('---\n# who answers\nSession_Agent_Speaks: "always"\n---\n')
    assert sa.speaks_policy(g) == "always"


def test_an_unknown_policy_value_is_logged_with_its_key_and_the_default(tmp_path, caplog):
    g = tmp_path / "_global"
    g.mkdir()
    (g / "POLICIES.md").write_text("---\nsession_agent_speaks: sometimes\n---\n")
    with caplog.at_level("WARNING", logger="agent_api.session_access"):
        assert sa.speaks_policy(g) == sa.DEFAULT_SPEAKS
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "session_agent_speaks" in msg and "'sometimes'" in msg and "mentioned" in msg


def test_a_shared_turn_reaches_the_speaker_s_other_chats_by_no_file_path(tmp_path, monkeypatch):
    """No mount of a shared turn holds the speaker's other conversations: `_system` (where the old
    layout kept them) is withheld, and with the store on the worker keeps the one it carries in a
    private folder outside every mount. The only way to another chat is `chat_read`, with its rules."""
    from worker import engine

    scope = sa.shared_scope("alice", others=["bob"], candidates=["alice", "team", "_global", "_system"],
                            is_member=_members({"team": {"alice", "bob"}}))
    mounts = [{"slug": "_global", "path": str(tmp_path / "g")}, {"slug": "team", "path": str(tmp_path / "t")},
              {"slug": "alice", "path": str(tmp_path / "a")}, {"slug": "_system", "role": "system", "path": str(tmp_path / "s")}]
    kept = sa.narrow_mounts(mounts, scope)
    assert all(m.get("role") != "system" for m in kept)
    monkeypatch.setattr(engine, "active_mounts", lambda: kept)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VEXA_SESSION_STORE", "1")
    engine.set_carry(None)
    root = engine._continuity_root(tmp_path / "t")
    assert not any(str(root).startswith(m["path"]) for m in kept)


def test_the_agent_is_told_chats_are_read_with_the_tools_only():
    from worker import engine
    assert "chats_search" in engine.CHATS_PREAMBLE and "chat_read" in engine.CHATS_PREAMBLE


def test_the_agent_is_never_told_chats_live_in_a_workspace():
    """session-store (0.13.3): `_system` holds who you help and settings; conversations are in the
    session store. Neither the tier's label nor the write-routing policy says otherwise."""
    from worker import engine
    label = engine._tier_label({"slug": "_system", "role": "system"})
    assert "chats" not in label.lower() or "not here" in label.lower()
    pre = engine.mounts_preamble([{"slug": "alice", "path": "/workspaces/alice", "write": True, "primary": True},
                                  {"slug": "_system", "role": "system", "path": "/workspaces/_system", "write": True}])
    assert "Chats/sessions/settings" not in pre
    assert "session store" in pre and "chats_search" in pre


def test_a_new_system_tier_does_not_claim_to_hold_chats(tmp_path):
    from control_plane import system_mounts
    home = system_mounts.ensure_system_workspace(str(tmp_path), "alice")
    text = (home / "README.md").read_text()
    assert text == system_mounts.SYSTEM_README and "session store" in text
