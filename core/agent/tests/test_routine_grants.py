"""A routine's delegated authority (flows phase 0, founder 2026-10-14).

A one-shot routine made in chat to send a bot at 12:13 fired, its `request_meeting_bot` was refused
`human_session_required`, and the run reported done. The fix is not to let unattended runs do more:
it is a named grant, recorded when the person creates the routine, minted only on that routine's own
autonomous turn, and honoured only for the verb it names.
"""
from __future__ import annotations

import pytest

from shared import delegation

SECRET = "test-secret"


def _scope(tok: str) -> dict:
    return delegation.verify_delegation(SECRET, tok)["scope"]


def test_a_routine_turn_carries_its_grants_beside_its_flow():
    tok = delegation.mint_delegation(SECRET, subject="7", regime="autonomous", workspaces=["7"],
                                     grants=["flow.retire_self", "bot.request", "bot.request"],
                                     flow="user:7/bot-at-1213")
    scope = _scope(tok)
    assert scope["grants"] == ["bot.request", "flow.retire_self"]
    assert scope["flow"] == "user:7/bot-at-1213"
    assert delegation.granted(scope, "bot.request")
    assert delegation.granted(scope, "flow.retire_self")
    assert not delegation.granted(scope, "bot.stop")


def test_no_grant_without_a_routine_and_never_on_a_watched_turn():
    with pytest.raises(ValueError):
        delegation.mint_delegation(SECRET, subject="7", regime="autonomous", workspaces=["7"],
                                   grants=["bot.request"])
    with pytest.raises(ValueError):
        delegation.mint_delegation(SECRET, subject="7", regime="human", grants=["bot.request"],
                                   flow="user:7/x")


def test_a_grant_outside_the_vocabulary_is_refused_never_guessed():
    with pytest.raises(ValueError, match="mail.send"):
        delegation.mint_delegation(SECRET, subject="7", regime="autonomous", workspaces=["7"],
                                   grants=["mail.send"], flow="user:7/x")
    assert not delegation.granted({"grants": ["mail.send"], "flow": "user:7/x"}, "mail.send")


def test_a_token_without_grants_is_byte_identical_to_before():
    a = delegation.mint_delegation(SECRET, subject="7", regime="autonomous", workspaces=["7"],
                                   now=1, jti="j")
    b = delegation.mint_delegation(SECRET, subject="7", regime="autonomous", workspaces=["7"],
                                   now=1, jti="j", grants=[], flow="")
    assert a == b
    scope = delegation.verify_delegation(SECRET, a, now=2)["scope"]
    assert "grants" not in scope and not delegation.granted(scope, "bot.request")


# ── agent-api mints the grant only on that routine's own unwatched turn ──────────────────────────
import json  # noqa: E402

from control_plane.dispatch import build_unit_env  # noqa: E402
from shared.config import load_settings  # noqa: E402

DLG = "test-delegation-secret-0123456789abcdef"
INV = {"identity": {"subject": "7", "launcher": "user:7"}, "runner": "openai-agent",
       "workspaces": [{"id": "7", "mode": "rw"}], "trigger": "scheduled",
       "context": {"kind": "generic", "session": "pchat-abc"},
       "start": {"entrypoint": {"inline": "send the bot"}}}


def _token_scope(tmp_path, monkeypatch, inv=INV, **kw) -> dict:
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("VEXA_MCP_URL", "http://mcp.test/mcp")
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), inv, unit_id="u", token="t", **kw)
    return delegation.verify_delegation(DLG, env["VEXA_MCP_DELEGATION_TOKEN"])["scope"]


def test_a_routine_turn_is_minted_with_its_grants(tmp_path, monkeypatch):
    scope = _token_scope(tmp_path, monkeypatch,
                         routine_grants=("user:7/bot-at-1213", ["bot.request", "mail.send"]))
    assert scope["grants"] == ["bot.request"] and scope["flow"] == "user:7/bot-at-1213"


def test_no_grant_on_a_watched_turn_or_an_event_agent(tmp_path, monkeypatch):
    grants = ("user:7/x", ["bot.request"])
    human = {**INV, "trigger": "message"}
    assert "grants" not in _token_scope(tmp_path, monkeypatch, inv=human, routine_grants=grants)
    assert "grants" not in _token_scope(tmp_path, monkeypatch, routine_grants=grants,
                                        least_privilege=True)
    assert "grants" not in _token_scope(tmp_path, monkeypatch)


def test_a_routine_in_a_room_keeps_its_toolbelt_and_carries_no_grant(tmp_path, monkeypatch):
    """A routine that runs in a room is the room's turn: shared, as the workspace's own principal
    (D2), which the minter never pairs with a grant. It runs with its toolbelt and without grants."""
    from control_plane.room_principal import RoomPrincipal
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("VEXA_MCP_URL", "http://mcp.test/mcp")
    (tmp_path / "team").mkdir()
    room = RoomPrincipal(workspace="team", owner="7", session="s-1", on_behalf_of="7")
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u", token="t",
                         room_principal=room, routine_grants=("user:7/bot-at-1213", ["bot.request"]))
    assert env.get("VEXA_MCP_DELEGATION_TOKEN"), "the room's routine turn lost its toolbelt"
    claims = delegation.verify_delegation(DLG, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["principal_kind"] == "workspace" and claims["sub"] != "7"
    assert "grants" not in claims["scope"] and claims["scope"]["shared"] is True


# ── how a routine's turn ended, for flows ─────────────────────────────────────────────────────────
from control_plane import api_shared  # noqa: E402


class _Stream:
    def __init__(self, events):
        self.rows = [(f"{i}-0", {"event": json.dumps(e)}) for i, e in enumerate(events, 1)]

    def xrevrange(self, *a, **k):
        return list(reversed(self.rows))


def _outcome(monkeypatch, events, turn="r1"):
    import redis
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _Stream(events))
    return api_shared.turn_outcome("redis://x", "unit", turn)


def test_a_refused_turn_reads_as_refused_not_done(monkeypatch):
    out = _outcome(monkeypatch, [
        {"type": "turn-accepted", "turn_id": "r1"},
        {"type": "tool-call", "callId": "c1", "tool": "request_meeting_bot", "turn_id": "r1"},
        {"type": "tool-result", "callId": "c1", "ok": False, "turn_id": "r1",
         "summary": "HTTP 403 human_session_required"},
        {"type": "done", "ok": True, "turn_id": "r1",
         "refused": {"tool": "request_meeting_bot", "reason": "human_session_required", "count": 1}},
    ])
    assert out["state"] == "done" and out["refused"]["reason"] == "human_session_required"
    assert out["acted"] == [] and out["failed"] == ["request_meeting_bot"]


def test_a_turn_still_running_and_another_turns_events(monkeypatch):
    assert _outcome(monkeypatch, [{"type": "turn-accepted", "turn_id": "r1"}])["state"] == "running"
    assert _outcome(monkeypatch, [{"type": "done", "ok": True, "turn_id": "r0"}])["state"] == "unknown"
    ok = _outcome(monkeypatch, [
        {"type": "tool-call", "callId": "c1", "tool": "request_meeting_bot", "turn_id": "r1"},
        {"type": "tool-result", "callId": "c1", "ok": True, "turn_id": "r1"},
        {"type": "done", "ok": True, "turn_id": "r1"}])
    assert ok["acted"] == ["request_meeting_bot"] and ok["refused"] is None


# ── the routine's card says what it may do ───────────────────────────────────────────────────────
from control_plane import routine_flows  # noqa: E402


def test_a_one_shot_bot_routine_is_on_the_board_and_says_what_it_may_do():
    f = {"name": "bot-at-1213", "version": 1, "scope": "user:7", "status": "active",
         "trigger": {"kind": "at", "at": 1803903180.0, "cron": "13 12 1 3 *"},
         "steps": ["request_meeting_bot"], "grants": ["bot.request"],
         "may": "This routine may: send bots to meetings.",
         "params": {"meeting_url": "https://meet.google.com/abc-defg-hij", "thread": "pchat-abc"}}

    class _Client(routine_flows.FlowsClient):
        def __init__(self):
            pass

        def _call(self, method, path, body=None):
            return {"flows": [f]}
    assert [x["name"] for x in _Client().routines("7")] == ["bot-at-1213"]
    card = routine_flows.card_from_flow("7", f)
    assert card["may"] == "This routine may: send bots to meetings."
    assert card["grants"] == ["bot.request"] and card["lifecycle"] == "once"
    assert card["thread"] == "pchat-abc"


def test_a_routine_turn_is_found_by_the_cursor_before_it_when_the_worker_names_it_t0(monkeypatch):
    """Found on app.dev (2026-10-14): a cold routine unit names its first turn `t0`, not the id flows
    posted, so the outcome was `unknown` forever. Flows passes the routine unit's stream tail it read
    before posting (`after`); the first turn the worker took after it is the run's."""
    import redis

    class _Ranged(_Stream):
        def xrange(self, stream, min="-", max="+", count=None):
            lo = min.lstrip("(")
            return [r for r in self.rows if int(r[0].split("-")[0]) > int(lo.split("-")[0] or 0)]

    events = [{"type": "done", "ok": True, "turn_id": "t0"},                  # an older turn, before `after`
              {"type": "turn-accepted", "turn_id": "t0"},
              {"type": "tool-call", "callId": "c1", "tool": "mcp__vexa__request_meeting_bot", "turn_id": "t0"},
              {"type": "tool-result", "callId": "c1", "ok": False, "turn_id": "t0",
               "summary": "HTTP 403 human_session_required"},
              {"type": "done", "ok": True, "turn_id": "t0",
               "refused": {"tool": "mcp__vexa__request_meeting_bot", "reason": "human_session_required", "count": 1}}]
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _Ranged(events))
    out = api_shared.turn_outcome("redis://x", "unit", "r-flows-id", after="1-0")
    assert out["state"] == "done" and out["refused"]["reason"] == "human_session_required"
    assert api_shared.turn_outcome("redis://x", "unit", "r-flows-id")["state"] == "unknown"
