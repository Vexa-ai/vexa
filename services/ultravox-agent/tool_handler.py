"""
Tool call router for Ultravox agent.

Routes tool invocations from Ultravox to the appropriate backend:
- trigger_agent → OpenClaw /v1/chat/completions (synchronous agent run)
- send_chat_message → WebSocket message to vexa-bot
- show_image → WebSocket message to vexa-bot
- get_meeting_context → Vexa API transcript endpoint
"""
import json
import logging
from typing import Any, Callable, Optional

import httpx

from config import VEXA_API_URL, OPENCLAW_WEBHOOK_URL, OPENCLAW_HOOKS_TOKEN

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
        """Trigger OpenClaw agent via /v1/chat/completions (synchronous)."""
        task = params.get("task", "")
        context = params.get("context", "")

        if not OPENCLAW_WEBHOOK_URL:
            logger.warning("[ToolHandler] OPENCLAW_WEBHOOK_URL not set, returning placeholder")
            return (
                "Agent backend not configured. The user asked for: "
                f"{task}. Please let them know you cannot perform this task right now "
                "but suggest they try again later."
            )

        # Fetch transcript to give OpenClaw meeting context
        transcript = await self._get_meeting_context()

        # Build message content: task + transcript + optional context
        message_parts = [f"Meeting ID: {self.meeting_id}"]
        if transcript and not transcript.startswith("Error") and not transcript.startswith("No transcript"):
            message_parts.append(f"Meeting transcript:\n{transcript}")
        if context:
            message_parts.append(f"Context: {context}")
        message_parts.append(f"Task: {task}")
        user_message = "\n".join(message_parts)

        # Use OpenClaw's OpenAI-compatible Chat Completions endpoint
        base_url = OPENCLAW_WEBHOOK_URL.rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
        }
        if OPENCLAW_HOOKS_TOKEN:
            headers["Authorization"] = f"Bearer {OPENCLAW_HOOKS_TOKEN}"

        payload = {
            "model": "openclaw",
            "messages": [{"role": "user", "content": user_message}],
        }

        logger.info(f"[ToolHandler] Triggering OpenClaw agent: task='{task[:80]}...'")
        logger.debug(f"[ToolHandler] OpenClaw URL: {url}")

        # Show "thinking" status on bot video while OpenClaw processes
        if self._send_to_bot:
            try:
                await self._send_to_bot({
                    "type": "meeting_action",
                    "action": "screen_show",
                    "content_type": "text",
                    "text": "🧠 Analyzing...",
                })
            except Exception:
                pass  # Non-critical — don't fail the tool call

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code != 200:
                    logger.error(
                        f"[ToolHandler] OpenClaw error {resp.status_code}: {resp.text[:300]}"
                    )
                    return f"Agent request failed (HTTP {resp.status_code}): {resp.text[:200]}"

                data = resp.json()
                # OpenAI Chat Completions response format
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        logger.info(
                            f"[ToolHandler] OpenClaw response: {content[:120]}..."
                        )
                        return content

                # Fallback: return raw response
                logger.warning(f"[ToolHandler] Unexpected OpenClaw response: {json.dumps(data)[:300]}")
                return json.dumps(data)[:2000]

        except httpx.ConnectError as e:
            logger.error(f"[ToolHandler] Cannot connect to OpenClaw at {base_url}: {e}")
            return (
                "Cannot reach the agent backend right now. "
                "Please let the user know and suggest trying again later."
            )
        except httpx.TimeoutException:
            logger.error(f"[ToolHandler] OpenClaw request timed out after 120s")
            return (
                "The agent backend took too long to respond. "
                "Please let the user know the request timed out."
            )
        finally:
            # Clear "thinking" status — return to avatar
            if self._send_to_bot:
                try:
                    await self._send_to_bot({
                        "type": "meeting_action",
                        "action": "screen_stop",
                    })
                except Exception:
                    pass

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
