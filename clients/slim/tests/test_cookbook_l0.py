"""L0 · Composition — every cookbook verb against a FakeSlim.

Proves each verb calls the right SDK methods, in the right order, with the right args, and returns the
right shape. No HTTP, no redis — the fixture boundary is the Slim sub-clients. One test per cookbook verb.
"""
from __future__ import annotations

import pytest

from vexa_slim import cookbook as cb
from vexa_slim.models import Harvest


# ── Identity & connect (bootstrap) — the inverted verb: PRODUCES a bound Slim ───────────────────────-
async def test_whoami_reads_auth_me(slim):
    me = await cb.whoami(slim)
    assert me["email"] == "fake@vexa.ai"
    assert "auth_me" in slim.names()


async def test_connect_requires_credentials():
    with pytest.raises(ValueError, match="api_key"):
        await cb.connect("http://gw")


async def test_connect_binds_key_and_verifies(monkeypatch):
    seen = {}

    async def fake_whoami(s):
        seen["base"], seen["key"] = s.base, s._headers["X-API-Key"]
        return {"user_id": 1}

    monkeypatch.setattr(cb, "whoami", fake_whoami)
    slim = await cb.connect("http://gw", api_key="k123")
    assert seen == {"base": "http://gw", "key": "k123"}  # bound + verified, no token logic in the cookbook


# ── Meeting (during) ────────────────────────────────────────────────────────────────────────────────
async def test_agent_on_meeting_with_url_sends_bot_then_starts(slim):
    out = await cb.agent_on_meeting(slim, "abc-defg-hij", meet_url="https://meet/x")
    assert slim.names() == ["send_bot", "start_processing"]   # bot first, then the processor
    assert slim.last("send_bot")["url"] == "https://meet/x"
    assert out["on"] is True


async def test_agent_on_meeting_without_url_skips_bot(slim):
    await cb.agent_on_meeting(slim, "abc")
    assert slim.names() == ["start_processing"]               # no bot when no url


async def test_listen_to_meeting_returns_harvest_and_stops(slim):
    out = await cb.listen_to_meeting(slim, "abc", seconds=1.0, meet_url="https://meet/x")
    # full happy path: bot → start → watch → stop
    assert slim.names() == ["send_bot", "start_processing", "watch", "stop_processing"]
    assert isinstance(out, Harvest)
    assert out.counts() == {"transcript": 1, "note": 1, "card": 2}
    assert len(out.of("card")) == 2
    assert slim.last("watch")["seconds"] == 1.0


async def test_meeting_doc_reads_the_durable_doc(slim):
    assert await cb.meeting_doc(slim, "abc") == "# meeting doc"
    assert slim.last("read_doc")["native"] == "abc"


# ── Chat & onboard (meta) ─────────────────────────────────────────────────────────────────────────--
async def test_chat_passes_through_session_active_files(slim):
    out = await cb.chat(slim, "hi", session="s1", active={"kind": "meeting"}, files=["a.md"])
    assert out == "REPLY"
    kw = slim.last("chat")
    assert (kw["prompt"], kw["session"], kw["active"], kw["files"]) == (
        "hi", "s1", {"kind": "meeting"}, ["a.md"])


async def test_onboard_injects_the_onboarding_playbook(slim):
    out = await cb.onboard(slim)
    assert out == "REPLY"
    assert slim.last("chat")["files"] == ["onboarding.md"]


# ── Cadence & automate ────────────────────────────────────────────────────────────────────────────--
async def test_schedule_routine_creates_a_routine(slim):
    out = await cb.schedule_routine(slim, "digest", cron="30 9 * * mon-fri", prompt="brief me", run_now=True)
    kw = slim.last("create_routine")
    assert (kw["name"], kw["cron"], kw["prompt"], kw["run_now"]) == (
        "digest", "30 9 * * mon-fri", "brief me", True)
    assert out["job_id"] == "job-1"


async def test_list_routines_returns_cards(slim):
    out = await cb.list_routines(slim)
    assert slim.names() == ["list_routines"]
    assert out[0]["name"] == "morning-digest"


async def test_set_routine_enabled_flips_the_flag(slim):
    out = await cb.set_routine_enabled(slim, "digest", False)
    kw = slim.last("set_routine_enabled")
    assert (kw["name"], kw["enabled"]) == ("digest", False)
    assert out["enabled"] is False


# ── Workspace controls ────────────────────────────────────────────────────────────────────────────--
async def test_init_workspace_calls_init(slim):
    out = await cb.init_workspace(slim)
    assert slim.names() == ["init_workspace"]
    assert out["seeded"] is True


async def test_mount_workspace_calls_swap(slim):
    await cb.mount_workspace(slim, repo="r", ref="main")
    assert slim.names() == ["swap_workspace"]   # wired to the swap endpoint (501 upstream until P6)


async def test_browse_workspace_returns_tree(slim):
    out = await cb.browse_workspace(slim)
    assert slim.names() == ["workspace_tree"]
    assert "CLAUDE.md" in out


async def test_read_workspace_file_returns_content(slim):
    out = await cb.read_workspace_file(slim, "agents/meeting.md")
    assert out == "contents of agents/meeting.md"
    assert slim.last("workspace_file")["path"] == "agents/meeting.md"
