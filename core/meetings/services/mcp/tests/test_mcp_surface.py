"""L1 — the MCP surface: the /mcp mount exists, exactly the 11 tools are exposed,
and the 5 prompts render."""
import httpx
import pytest

from vexa_mcp import create_app
from vexa_mcp.prompts import PROMPTS, get_prompt_result

EXPECTED_TOOLS = {
    "auth_me",
    "parse_meeting_link",
    "request_meeting_bot",
    "get_bot_status",
    "update_bot_config",
    "stop_bot",
    "list_meetings",
    "get_meeting_transcript",
    "list_recordings",
    "get_recording",
    "report_issue",
}


def test_mcp_mounted_and_tools_match():
    app = create_app("http://gateway.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    mcp = app.state.mcp
    assert {t.name for t in mcp.tools} == EXPECTED_TOOLS
    # The MCP transport is mounted on the app at /mcp.
    assert any(getattr(r, "path", "") == "/mcp" for r in app.routes)


def test_prompt_catalog():
    assert set(PROMPTS) == {
        "vexa.capabilities",
        "vexa.meeting_prep",
        "vexa.during_meeting",
        "vexa.post_meeting",
        "vexa.teams_link_help",
    }


def test_capabilities_prompt_states_the_auth_contract():
    """The orientation prompt exists to answer four questions an agent otherwise guesses at:
    where to call, what carries the key, which scopes there are, and what a 403 means. It must
    also refuse the prefix heuristic explicitly — a `vxa_bot_` key that does not carry `bot` is
    the exact case that sends an agent round a retry loop it cannot win."""
    text = get_prompt_result("vexa.capabilities").messages[0].content.text
    assert "https://api.cloud.vexa.ai" in text          # where
    assert "VEXA_API_KEY" in text                        # the credential's env var
    assert "Authorization: Bearer" in text               # how it is sent
    for scope in ("`bot`", "`tx`", "`browser`"):         # the three scopes, each named
        assert scope in text
    assert "`required`" in text and "`granted`" in text  # how to read a 403 body
    assert "`auth_me`" in text                           # the authoritative capability read
    # The prefix is a hint; the scopes column decides (admin_api/token_scope.py).
    assert "vxa_<scope>_" in text
    assert "authoritative" in text


def test_capabilities_prompt_takes_a_self_hosted_base_url():
    """A self-hoster's agent must not be told to call the hosted API."""
    text = get_prompt_result(
        "vexa.capabilities", {"api_base_url": "https://vexa.internal.example"}
    ).messages[0].content.text
    assert "https://vexa.internal.example" in text
    assert "https://api.cloud.vexa.ai" not in text


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompts_render(name):
    result = get_prompt_result(name, {
        "meeting_url": "https://teams.live.com/meet/9361792952021?p=x",
        "meeting_platform": "teams",
        "meeting_id": "9361792952021",
        "notes": "quarterly sync",
    })
    assert result.messages, name
    text = result.messages[0].content.text
    assert text.strip()


def test_prompts_only_reference_ported_tools():
    """A prompt must not instruct a tool that was NOT ported (README: blocked on API parity)."""
    skipped = {
        "get_meeting_bundle", "create_transcript_share_link", "update_meeting_data",
        "delete_meeting", "delete_recording", "get_recording_media_download",
        "get_recording_config", "update_recording_config",
    }
    for name in PROMPTS:
        text = get_prompt_result(name, {}).messages[0].content.text
        for tool in skipped:
            assert f"`{tool}`" not in text, f"prompt {name} references skipped tool {tool}"


def test_unknown_prompt_raises():
    with pytest.raises(ValueError):
        get_prompt_result("vexa.nope")
