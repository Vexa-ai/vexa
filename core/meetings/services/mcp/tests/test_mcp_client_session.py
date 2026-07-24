"""Protocol-level witness for the mounted streamable-HTTP MCP service."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from vexa_mcp import create_app

from conftest import API_KEY, GATEWAY_URL


@pytest.mark.asyncio
async def test_real_mcp_client_session_exercises_tools_and_prompts(gateway):
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mcp.test",
            headers={"Authorization": f"Bearer {API_KEY}"},
            follow_redirects=True,
        ) as mcp_http_client:
            async with streamable_http_client(
                "http://mcp.test/mcp",
                http_client=mcp_http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    listed = await session.list_tools()
                    tools = {tool.name: tool for tool in listed.tools}
                    assert set(tools) == {
                        "parse_meeting_link",
                        "request_meeting_bot",
                        "get_bot_status",
                        "update_bot_config",
                        "stop_bot",
                        "list_meetings",
                        "get_meeting_transcript",
                        "list_recordings",
                        "get_recording",
                    }
                    assert tools["parse_meeting_link"].inputSchema["required"] == ["meeting_url"]

                    parsed = await session.call_tool(
                        "parse_meeting_link",
                        {"meeting_url": "https://meet.google.com/abc-defg-hij"},
                    )
                    assert parsed.isError is False
                    parsed_payload = json.loads(parsed.content[0].text)
                    assert parsed_payload["platform"] == "google_meet"
                    assert parsed_payload["native_meeting_id"] == "abc-defg-hij"

                    requested = await session.call_tool(
                        "request_meeting_bot",
                        {"meeting_url": "https://meet.google.com/abc-defg-hij"},
                    )
                    assert requested.isError is False
                    assert json.loads(requested.content[0].text) == {
                        "ok": True,
                        "path": "/bots",
                    }
                    assert gateway.requests[-1].headers["X-API-Key"] == API_KEY
                    assert gateway.last_json()["native_meeting_id"] == "abc-defg-hij"

                    prompts = await session.list_prompts()
                    assert {prompt.name for prompt in prompts.prompts} == {
                        "vexa.meeting_prep",
                        "vexa.during_meeting",
                        "vexa.post_meeting",
                        "vexa.teams_link_help",
                    }
                    prompt = await session.get_prompt(
                        "vexa.meeting_prep",
                        {"meeting_url": "https://meet.google.com/abc-defg-hij"},
                    )
                    assert prompt.messages
                    assert "abc-defg-hij" in json.dumps(prompt.model_dump())
