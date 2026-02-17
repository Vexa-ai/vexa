"""
Ultravox REST API + WebSocket client.

Creates Ultravox calls and manages the bidirectional WebSocket connection
for audio streaming and tool call handling.
"""
import asyncio
import json
import logging
import struct
from typing import Callable, Optional, List, Dict, Any

import httpx
import websockets

from config import (
    ULTRAVOX_API_KEY,
    ULTRAVOX_API_URL,
    ULTRAVOX_MODEL,
    ULTRAVOX_VOICE,
    ULTRAVOX_TEMPERATURE,
    ULTRAVOX_LANGUAGE_HINT,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    CLIENT_BUFFER_SIZE_MS,
    DEFAULT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


class UltravoxCall:
    """Manages a single Ultravox call: REST creation + WebSocket lifecycle."""

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        input_sample_rate: int = INPUT_SAMPLE_RATE,
        output_sample_rate: int = OUTPUT_SAMPLE_RATE,
    ):
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.tools = tools or self._default_tools()
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate

        self._ws = None  # websockets ClientConnection
        self._join_url: Optional[str] = None
        self._call_id: Optional[str] = None
        self._state: str = "disconnected"
        self._running = False

        # Callbacks
        self._on_agent_audio: Optional[Callable[[bytes], Any]] = None
        self._on_tool_call: Optional[Callable[[str, str, dict], Any]] = None
        self._on_transcript: Optional[Callable[[dict], Any]] = None
        self._on_state_change: Optional[Callable[[str], Any]] = None

    @staticmethod
    def _default_tools() -> List[Dict[str, Any]]:
        """Default tool definitions for the Vexa meeting assistant."""
        return [
            {
                "temporaryTool": {
                    "modelToolName": "trigger_agent",
                    "description": (
                        "Trigger a backend agent to perform a complex task like research, "
                        "document generation, task creation, or analysis. The agent has access "
                        "to the full meeting transcript with speaker names. Returns the result text. "
                        "This tool may take up to 2 minutes to complete — do NOT retry or call it again while waiting."
                    ),
                    "dynamicParameters": [
                        {
                            "name": "task",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {"type": "string", "description": "Description of the task to perform"},
                            "required": True,
                        },
                        {
                            "name": "context",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {"type": "string", "description": "Additional context from the conversation"},
                            "required": False,
                        },
                    ],
                    "timeout": "120s",
                    "client": {},
                }
            },
            {
                "temporaryTool": {
                    "modelToolName": "send_chat_message",
                    "description": (
                        "Send a text message in the meeting chat. Use for links, "
                        "formatted summaries, code snippets — things better read than heard."
                    ),
                    "dynamicParameters": [
                        {
                            "name": "text",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {"type": "string", "description": "The message text to send"},
                            "required": True,
                        },
                    ],
                    "client": {},
                }
            },
            {
                "temporaryTool": {
                    "modelToolName": "show_image",
                    "description": (
                        "Display an image on the bot's camera feed in the meeting. "
                        "Use for charts, screenshots, diagrams."
                    ),
                    "dynamicParameters": [
                        {
                            "name": "url",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {"type": "string", "description": "URL of the image to display"},
                            "required": True,
                        },
                    ],
                    "client": {},
                }
            },
            {
                "temporaryTool": {
                    "modelToolName": "get_meeting_context",
                    "description": (
                        "Fetch the recent meeting transcript with speaker names. "
                        "Use when you need to reference who said what."
                    ),
                    "dynamicParameters": [],
                    "client": {},
                }
            },
        ]

    async def create(self) -> str:
        """Create an Ultravox call via REST API. Returns the joinUrl."""
        if not ULTRAVOX_API_KEY:
            raise RuntimeError("ULTRAVOX_API_KEY not set")

        body = {
            "systemPrompt": self.system_prompt,
            "model": ULTRAVOX_MODEL,
            "voice": ULTRAVOX_VOICE,
            "temperature": ULTRAVOX_TEMPERATURE,
            "languageHint": ULTRAVOX_LANGUAGE_HINT,
            "medium": {
                "serverWebSocket": {
                    "inputSampleRate": self.input_sample_rate,
                    "outputSampleRate": self.output_sample_rate,
                    "clientBufferSizeMs": CLIENT_BUFFER_SIZE_MS,
                }
            },
            "selectedTools": self.tools,
        }

        url = f"{ULTRAVOX_API_URL.rstrip('/')}/calls"
        headers = {
            "X-API-Key": ULTRAVOX_API_KEY,
            "Content-Type": "application/json",
        }

        logger.info(f"[Ultravox] Creating call: model={ULTRAVOX_MODEL}, voice={ULTRAVOX_VOICE}")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Ultravox API error {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        self._join_url = data.get("joinUrl")
        self._call_id = data.get("callId") or data.get("id")
        if not self._join_url:
            raise RuntimeError(f"No joinUrl in response: {data}")

        logger.info(f"[Ultravox] Call created: id={self._call_id}, joinUrl={self._join_url[:80]}...")
        return self._join_url

    async def connect(self) -> None:
        """Connect to the Ultravox WebSocket using the joinUrl."""
        if not self._join_url:
            raise RuntimeError("Call not created yet — call create() first")

        ws_url = self._join_url
        if "?" in ws_url:
            ws_url += "&apiVersion=1"
        else:
            ws_url += "?apiVersion=1"

        logger.info(f"[Ultravox] Connecting WebSocket...")
        self._ws = await websockets.connect(ws_url, max_size=2**20)
        self._state = "connecting"
        self._running = True
        logger.info(f"[Ultravox] WebSocket connected")

    async def receive_loop(self) -> None:
        """Main receive loop — dispatches incoming messages."""
        if not self._ws:
            raise RuntimeError("Not connected")

        try:
            async for message in self._ws:
                if not self._running:
                    break

                if isinstance(message, bytes):
                    # Binary: agent audio PCM
                    if self._on_agent_audio:
                        await _maybe_await(self._on_agent_audio(message))
                else:
                    # Text: JSON control message
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        logger.warning(f"[Ultravox] Non-JSON text frame: {message[:100]}")
                        continue
                    await self._handle_json_message(data)
        except websockets.ConnectionClosed as e:
            logger.info(f"[Ultravox] WebSocket closed: code={e.code} reason={e.reason}")
        except Exception as e:
            logger.error(f"[Ultravox] Receive loop error: {e}", exc_info=True)
        finally:
            self._state = "disconnected"
            self._running = False

    async def _handle_json_message(self, data: dict) -> None:
        msg_type = data.get("type", "")

        if msg_type == "state":
            new_state = data.get("state", "")
            old_state = self._state
            self._state = new_state
            logger.debug(f"[Ultravox] State: {old_state} → {new_state}")
            if self._on_state_change:
                await _maybe_await(self._on_state_change(new_state))

        elif msg_type == "transcript":
            text = data.get("text") or ""
            is_final = data.get("isFinal", False)
            # Only log transcripts with actual text to reduce noise
            if is_final or text.strip():
                logger.info(
                    f"[Ultravox] Transcript: role={data.get('role')} "
                    f"final={is_final} text={text[:80]}"
                )
            if self._on_transcript:
                await _maybe_await(self._on_transcript(data))

        elif msg_type == "client_tool_invocation":
            tool_name = data.get("toolName", "")
            invocation_id = data.get("invocationId", "")
            raw_params = data.get("parameters", {})
            # Ultravox may send parameters as JSON string or dict
            if isinstance(raw_params, str):
                try:
                    parameters = json.loads(raw_params)
                except json.JSONDecodeError:
                    parameters = {"raw": raw_params}
            else:
                parameters = raw_params or {}
            logger.info(f"[Ultravox] Tool call: {tool_name} (id={invocation_id})")
            if self._on_tool_call:
                await _maybe_await(self._on_tool_call(tool_name, invocation_id, parameters))

        elif msg_type == "playback_clear_buffer":
            logger.info("[Ultravox] Playback clear buffer (user barge-in)")
            if self._on_state_change:
                await _maybe_await(self._on_state_change("interrupted"))

        else:
            logger.info(f"[Ultravox] Unknown message type: {msg_type} data={str(data)[:200]}")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send audio data (already in Int16LE PCM format) to Ultravox."""
        if self._ws and self._running:
            try:
                await self._ws.send(pcm_bytes)
            except websockets.ConnectionClosed:
                logger.warning("[Ultravox] Cannot send audio — connection closed")
            except Exception as e:
                logger.error(f"[Ultravox] Error sending audio: {e}")

    async def send_tool_result(
        self, invocation_id: str, result: str, reaction: str = "speaks"
    ) -> None:
        """Send a tool call result back to Ultravox."""
        msg = {
            "type": "client_tool_result",
            "invocationId": invocation_id,
            "result": result,
            "agentReaction": reaction,
        }
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps(msg))
                logger.info(f"[Ultravox] Tool result sent: id={invocation_id} reaction={reaction}")
            except Exception as e:
                logger.error(f"[Ultravox] Error sending tool result: {e}")

    async def send_text_input(self, text: str) -> None:
        """Inject text input into the Ultravox conversation (e.g. transcript context)."""
        msg = {
            "type": "input_text_message",
            "text": text,
        }
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps(msg))
                logger.info(f"[Ultravox] Text input sent: {len(text)} chars")
            except Exception as e:
                logger.error(f"[Ultravox] Error sending text input: {e}")

    async def hangup(self) -> None:
        """Disconnect from the Ultravox call."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._state = "disconnected"
        logger.info("[Ultravox] Call ended")

    # --- Callback registration ---

    def on_agent_audio(self, callback: Callable[[bytes], Any]) -> None:
        self._on_agent_audio = callback

    def on_tool_call(self, callback: Callable[[str, str, dict], Any]) -> None:
        self._on_tool_call = callback

    def on_transcript(self, callback: Callable[[dict], Any]) -> None:
        self._on_transcript = callback

    def on_state_change(self, callback: Callable[[str], Any]) -> None:
        self._on_state_change = callback

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._running


async def _maybe_await(result):
    """Await if result is a coroutine, otherwise return as-is."""
    if asyncio.iscoroutine(result):
        return await result
    return result
