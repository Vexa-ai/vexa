"""L1 — the MCP surface: the /mcp mount exists, exactly the 13 tools are exposed,
and the 4 prompts render."""
import httpx
import pytest

from vexa_mcp import create_app
from vexa_mcp.prompts import PROMPTS, get_prompt_result

EXPECTED_TOOLS = {
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
    "annotate_meeting",
    "speak_in_meeting",
    "get_meeting_chat",
}


def test_mcp_mounted_and_tools_match():
    app = create_app("http://gateway.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    mcp = app.state.mcp
    assert {t.name for t in mcp.tools} == EXPECTED_TOOLS
    # The MCP transport is mounted on the app at /mcp.
    assert any(getattr(r, "path", "") == "/mcp" for r in app.routes)


def test_prompt_catalog():
    assert set(PROMPTS) == {
        "vexa.meeting_prep",
        "vexa.during_meeting",
        "vexa.post_meeting",
        "vexa.teams_link_help",
    }


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


# --- orientation at connect time ---------------------------------------------
# A client that connects should not have to infer what Vexa is from nine tool descriptions.
# `instructions` is the MCP field for that, and it shipped empty.

def test_server_ships_instructions():
    app = create_app("http://gateway.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    instructions = app.state.mcp.server.instructions
    assert instructions, "MCP server must ship `instructions` — a connecting client gets no map without it"
    # The canonical flow and the identity vocabulary are the two things it exists to say.
    for expected in ("parse_meeting_link", "request_meeting_bot", "get_meeting_transcript", "native_meeting_id"):
        assert expected in instructions
