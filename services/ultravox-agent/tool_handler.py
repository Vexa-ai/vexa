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
        api_key: str = "",
        platform: str = "",
        native_meeting_id: str = "",
        send_to_bot: Optional[Callable[[dict], Any]] = None,
    ):
        self.meeting_id = meeting_id
        self.token = token
        self.api_key = api_key
        self.platform = platform
        self.native_meeting_id = native_meeting_id
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
        message_parts = [f"Meeting: platform={self.platform}, native_meeting_id={self.native_meeting_id}, db_id={self.meeting_id}"]
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
            "stream": True,
        }

        logger.info(f"[ToolHandler] Triggering OpenClaw agent: task='{task[:80]}'")
        logger.debug(f"[ToolHandler] OpenClaw URL: {url}")

        # Show task clearly on bot screen while waiting
        task_preview = task[:120] + ("..." if len(task) > 120 else "")
        await self._update_bot_screen(f"# 🧠 Working on it...\n\n{task_preview}")

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        logger.error(
                            f"[ToolHandler] OpenClaw error {resp.status_code}: {body[:300]}"
                        )
                        return f"Agent request failed (HTTP {resp.status_code}): {body[:200].decode()}"

                    full_content = []
                    last_screen_update = ""
                    chars_since_update = 0

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if not token:
                            continue

                        full_content.append(token)
                        chars_since_update += len(token)

                        # Update screen every ~30 chars to avoid flooding
                        if chars_since_update >= 30:
                            chars_since_update = 0
                            accumulated = "".join(full_content)
                            if accumulated != last_screen_update:
                                last_screen_update = accumulated
                                await self._update_bot_screen(accumulated)

                    content = "".join(full_content)
                    if content:
                        logger.info(f"[ToolHandler] OpenClaw response: {content[:120]}...")
                        return content

                    logger.warning("[ToolHandler] OpenClaw stream ended with no content")
                    return "Agent returned no response."

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
            # Clear status — return to avatar
            if self._send_to_bot:
                try:
                    await self._send_to_bot({
                        "type": "meeting_action",
                        "action": "screen_stop",
                    })
                except Exception:
                    pass

    async def _update_bot_screen(self, text: str) -> None:
        """Update the bot screen with text. Non-critical — errors are swallowed."""
        if not self._send_to_bot:
            return
        try:
            await self._send_to_bot({
                "type": "meeting_action",
                "action": "screen_show",
                "content_type": "text",
                "text": text,
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
        if not self.platform or not self.native_meeting_id:
            logger.warning("[ToolHandler] Missing platform or native_meeting_id for transcript fetch")
            return "No transcript available (missing meeting identifiers)."

        url = f"{VEXA_API_URL.rstrip('/')}/transcripts/{self.platform}/{self.native_meeting_id}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={"X-API-Key": self.api_key},
                )
                if resp.status_code != 200:
                    logger.warning(f"[ToolHandler] Transcript fetch failed: HTTP {resp.status_code} from {url}")
                    return f"Could not fetch transcript (HTTP {resp.status_code})"

                data = resp.json()
                segments = data.get("segments", [])

                if not segments:
                    return "No transcript available yet."

                # Format as readable text with speaker names
                lines = []
                for seg in segments[-30:]:  # Last 30 segments
                    speaker = seg.get("speaker", "Unknown")
                    text = seg.get("text", "")
                    if text.strip():
                        lines.append(f"{speaker}: {text.strip()}")

                return "\n".join(lines) if lines else "No transcript content yet."

        except Exception as e:
            logger.error(f"[ToolHandler] Error fetching transcript: {e}")
            return f"Error fetching meeting context: {str(e)}"
