"""
Tool call router for Ultravox agent.

Routes tool invocations from Ultravox to the appropriate backend:
- trigger_agent → Vexa API → OpenClaw webhook
- send_chat_message → WebSocket message to vexa-bot
- show_image → WebSocket message to vexa-bot
- get_meeting_context → Vexa API transcript endpoint
"""
import json
import logging
from typing import Any, Callable, Optional

import httpx

from config import VEXA_API_URL, OPENCLAW_WEBHOOK_URL

logger = logging.getLogger(__name__)


class ToolHandler:
    """Handles tool calls from Ultravox and routes them to appropriate backends."""

    def __init__(
        self,
        meeting_id: int,
        token: str,
        send_to_bot: Optional[Callable[[dict], Any]] = None,
    ):
        self.meeting_id = meeting_id
        self.token = token
        self._send_to_bot = send_to_bot

    async def handle(self, tool_name: str, invocation_id: str, parameters: dict) -> str:
        """Route a tool call and return the result string."""
        try:
            if tool_name == "trigger_agent":
                return await self._trigger_agent(parameters)
            elif tool_name == "send_chat_message":
                return await self._send_chat_message(parameters)
            elif tool_name == "show_image":
                return await self._show_image(parameters)
            elif tool_name == "get_meeting_context":
                return await self._get_meeting_context()
            else:
                logger.warning(f"[ToolHandler] Unknown tool: {tool_name}")
                return f"Error: unknown tool '{tool_name}'"
        except Exception as e:
            logger.error(f"[ToolHandler] Error handling {tool_name}: {e}")
            return f"Error executing {tool_name}: {str(e)}"

    async def _trigger_agent(self, params: dict) -> str:
        """Trigger OpenClaw agent via Vexa API webhook."""
        task = params.get("task", "")
        context = params.get("context", "")

        if not OPENCLAW_WEBHOOK_URL:
            # Fallback: if no OpenClaw configured, use Vexa API directly
            logger.warning("[ToolHandler] OPENCLAW_WEBHOOK_URL not set, returning placeholder")
            return (
                "Agent backend not configured. The user asked for: "
                f"{task}. Please let them know you cannot perform this task right now "
                "but suggest they try again later."
            )

        payload = {
            "task": task,
            "context": context,
            "meeting_id": self.meeting_id,
            "source": "ultravox-agent",
        }

        logger.info(f"[ToolHandler] Triggering agent: task='{task[:80]}...'")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OPENCLAW_WEBHOOK_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code not in (200, 201, 202):
                return f"Agent request failed (HTTP {resp.status_code}): {resp.text[:200]}"

            try:
                data = resp.json()
                return data.get("result", data.get("message", json.dumps(data)))
            except Exception:
                return resp.text[:2000]

    async def _send_chat_message(self, params: dict) -> str:
        """Send a chat message in the meeting via the bot."""
        text = params.get("text", "")
        if not text:
            return "No text provided"

        if self._send_to_bot:
            await self._send_to_bot({
                "type": "meeting_action",
                "action": "chat_send",
                "text": text,
            })
            return f"Chat message sent: {text[:100]}"
        else:
            return "Bot connection not available — cannot send chat message"

    async def _show_image(self, params: dict) -> str:
        """Show an image on the bot's camera feed."""
        url = params.get("url", "")
        if not url:
            return "No image URL provided"

        if self._send_to_bot:
            await self._send_to_bot({
                "type": "meeting_action",
                "action": "screen_show",
                "content_type": "image",
                "url": url,
            })
            return f"Image displayed: {url[:100]}"
        else:
            return "Bot connection not available — cannot show image"

    async def _get_meeting_context(self) -> str:
        """Fetch recent transcript with speaker names from Vexa API."""
        url = f"{VEXA_API_URL.rstrip('/')}/api/meetings/{self.meeting_id}/transcript"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                    params={"limit": 50},  # Last 50 segments
                )
                if resp.status_code != 200:
                    return f"Could not fetch transcript (HTTP {resp.status_code})"

                data = resp.json()
                segments = data.get("segments", data.get("results", []))

                if not segments:
                    return "No transcript available yet."

                # Format as readable text with speaker names
                lines = []
                for seg in segments[-30:]:  # Last 30 segments
                    speaker = seg.get("speaker_name", seg.get("speaker", "Unknown"))
                    text = seg.get("text", "")
                    if text.strip():
                        lines.append(f"{speaker}: {text.strip()}")

                return "\n".join(lines) if lines else "No transcript content yet."

        except Exception as e:
            logger.error(f"[ToolHandler] Error fetching transcript: {e}")
            return f"Error fetching meeting context: {str(e)}"
