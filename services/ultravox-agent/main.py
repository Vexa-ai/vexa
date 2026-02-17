"""
Ultravox Agent Service

WebSocket server that bridges vexa-bot audio to Ultravox voice AI.
Follows the same pattern as WhisperLive — bot sends audio, service processes it.

Bot → Service: Float32 audio frames + JSON control messages
Service → Bot: Int16LE PCM agent audio + JSON control/action messages
"""
import asyncio
import json
import logging
import struct
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import numpy as np
import websockets

from config import WS_PORT, HEALTH_PORT, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE
from ultravox_client import UltravoxCall
from tool_handler import ToolHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global health state
is_healthy = False


class BotSession:
    """Manages a single bot↔Ultravox session."""

    def __init__(self, bot_ws):
        self.bot_ws = bot_ws
        self.ultravox_call: Optional[UltravoxCall] = None
        self.tool_handler: Optional[ToolHandler] = None
        self._ultravox_task: Optional[asyncio.Task] = None
        self._config: dict = {}
        self._paused: bool = True  # starts paused — activated via /on chat command

    async def start(self, config: dict) -> None:
        """Initialize Ultravox call based on bot config."""
        self._config = config
        meeting_id = config.get("meeting_id", 0)
        token = config.get("token", "")
        system_prompt = config.get("ultravox_system_prompt")

        logger.info(
            f"[Session] Starting for meeting={meeting_id} "
            f"platform={config.get('platform', 'unknown')} "
            f"native_meeting_id={config.get('native_meeting_id', '')} "
            f"api_key={'present' if config.get('api_key') else 'MISSING'}"
        )

        # Tool handler routes tool calls back to bot or to external APIs
        self.tool_handler = ToolHandler(
            meeting_id=meeting_id,
            token=token,
            api_key=config.get("api_key", ""),
            platform=config.get("platform", ""),
            native_meeting_id=config.get("native_meeting_id", ""),
            send_to_bot=self._send_json_to_bot,
        )

        # Create Ultravox call
        self.ultravox_call = UltravoxCall(
            system_prompt=system_prompt,
            input_sample_rate=INPUT_SAMPLE_RATE,
            output_sample_rate=OUTPUT_SAMPLE_RATE,
        )

        # Register callbacks
        self.ultravox_call.on_agent_audio(self._on_agent_audio)
        self.ultravox_call.on_tool_call(self._on_tool_call)
        self.ultravox_call.on_transcript(self._on_transcript)
        self.ultravox_call.on_state_change(self._on_state_change)

        # Create and connect
        try:
            await self.ultravox_call.create()
            await self.ultravox_call.connect()

            # Notify bot
            await self._send_json_to_bot({"status": "ULTRAVOX_CONNECTED"})

            # Start receive loop in background
            self._ultravox_task = asyncio.create_task(self.ultravox_call.receive_loop())

        except Exception as e:
            logger.error(f"[Session] Failed to create Ultravox call: {e}")
            await self._send_json_to_bot({"status": "ERROR", "message": str(e)})
            raise

    async def handle_bot_message(self, message) -> bool:
        """Process a message from the bot. Returns False if session should end."""
        if isinstance(message, bytes):
            # Binary: audio data (Float32 from bot)
            await self._forward_audio(message)
            return True

        # Text: JSON control message
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"[Session] Non-JSON text from bot: {message[:100]}")
            return True

        msg_type = data.get("type", "")

        if msg_type == "session_control":
            event = data.get("payload", {}).get("event", "")
            if event == "LEAVING_MEETING":
                logger.info("[Session] Bot leaving meeting — ending Ultravox call")
                return False

        if msg_type == "pause":
            self._paused = True
            logger.info("[Session] PAUSED — audio forwarding stopped")
            return True

        if msg_type == "resume":
            self._paused = False
            logger.info("[Session] RESUMED — audio forwarding active")
            # Inject meeting transcript as context on activation
            asyncio.create_task(self._inject_transcript_context())
            return True

        return True

    _fwd_audio_count = 0

    async def _forward_audio(self, float32_bytes: bytes) -> None:
        """Convert Float32 PCM from bot to Int16LE and forward to Ultravox."""
        if self._paused:
            return
        if not self.ultravox_call or not self.ultravox_call.is_connected:
            return

        self._fwd_audio_count += 1
        if self._fwd_audio_count <= 3 or self._fwd_audio_count % 500 == 0:
            logger.info(f"[Session] Forwarding audio frame #{self._fwd_audio_count}: {len(float32_bytes)} bytes")

        try:
            # Convert Float32 → Int16LE
            float_array = np.frombuffer(float32_bytes, dtype=np.float32)
            int16_array = np.clip(float_array * 32767, -32768, 32767).astype(np.int16)
            await self.ultravox_call.send_audio(int16_array.tobytes())
        except Exception as e:
            logger.error(f"[Session] Audio forward error: {e}")

    _audio_frame_count = 0

    async def _on_agent_audio(self, pcm_data: bytes) -> None:
        """Receive agent speech from Ultravox, forward to bot as binary."""
        if self._paused:
            return
        self._audio_frame_count += 1
        if self._audio_frame_count <= 3 or self._audio_frame_count % 100 == 0:
            logger.info(f"[Session] Agent audio frame #{self._audio_frame_count}: {len(pcm_data)} bytes")
        try:
            await self.bot_ws.send(pcm_data)
        except websockets.ConnectionClosed:
            logger.warning("[Session] Bot disconnected while sending agent audio")
        except Exception as e:
            logger.error(f"[Session] Error sending agent audio to bot: {e}")

    async def _on_tool_call(
        self, tool_name: str, invocation_id: str, parameters: dict
    ) -> None:
        """Handle tool call from Ultravox — route and return result.

        Runs in a background task so long-running tools (like trigger_agent
        calling OpenClaw) don't block the WebSocket receive loop and cause
        keepalive ping timeouts.
        """
        if not self.tool_handler or not self.ultravox_call:
            return

        asyncio.create_task(
            self._execute_tool(tool_name, invocation_id, parameters)
        )

    async def _execute_tool(
        self, tool_name: str, invocation_id: str, parameters: dict
    ) -> None:
        """Execute a tool call and send the result back to Ultravox."""
        try:
            result = await self.tool_handler.handle(tool_name, invocation_id, parameters)

            # Determine reaction: speak for trigger_agent/get_meeting_context, listen for actions
            if tool_name in ("send_chat_message", "show_image"):
                reaction = "listens"
            else:
                reaction = "speaks"

            await self.ultravox_call.send_tool_result(invocation_id, result, reaction)
        except Exception as e:
            logger.error(f"[Session] Tool execution error for {tool_name}: {e}", exc_info=True)
            try:
                await self.ultravox_call.send_tool_result(
                    invocation_id,
                    f"Error executing {tool_name}: {str(e)}",
                    "speaks",
                )
            except Exception:
                pass

    async def _on_transcript(self, data: dict) -> None:
        """Forward only final transcript events to bot (skip noisy incremental updates)."""
        if not data.get("isFinal"):
            return  # Skip non-final transcripts — they flood the bot WebSocket
        text = data.get("text", "").strip()
        if not text:
            return  # Skip empty transcripts
        await self._send_json_to_bot({
            "type": "ultravox_transcript",
            "role": data.get("role", ""),
            "text": text,
            "isFinal": True,
        })

    async def _inject_transcript_context(self) -> None:
        """Fetch meeting transcript and inject as text context into Ultravox on activation."""
        if not self.tool_handler or not self.ultravox_call:
            return
        try:
            transcript = await self.tool_handler._get_meeting_context()
            if transcript and not transcript.startswith("Error") and not transcript.startswith("No transcript") and not transcript.startswith("Could not"):
                context_msg = (
                    f"[MEETING CONTEXT] Here is the meeting transcript so far. "
                    f"Use this to answer questions about what was discussed:\n\n{transcript}"
                )
                await self.ultravox_call.send_text_input(context_msg)
                logger.info(f"[Session] Injected transcript context: {len(transcript)} chars")
            else:
                logger.info(f"[Session] No transcript to inject: {transcript[:80] if transcript else 'empty'}")
        except Exception as e:
            logger.error(f"[Session] Failed to inject transcript context: {e}")

    async def _on_state_change(self, new_state: str) -> None:
        """Forward state changes to bot for mic control and interrupt handling."""
        await self._send_json_to_bot({"type": "ultravox_state", "state": new_state})

        # Tell bot when agent starts/stops speaking (for mic mute/unmute)
        if new_state == "speaking":
            await self._send_json_to_bot({"type": "agent_speaking"})
        elif new_state in ("listening", "idle"):
            await self._send_json_to_bot({"type": "agent_done_speaking"})
        elif new_state == "interrupted":
            await self._send_json_to_bot({"type": "agent_interrupted"})

    async def _send_json_to_bot(self, data: dict) -> None:
        """Send a JSON message to the bot."""
        try:
            await self.bot_ws.send(json.dumps(data))
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"[Session] Error sending to bot: {e}")

    async def cleanup(self) -> None:
        """Clean up Ultravox call and tasks."""
        if self.ultravox_call:
            await self.ultravox_call.hangup()

        if self._ultravox_task and not self._ultravox_task.done():
            self._ultravox_task.cancel()
            try:
                await self._ultravox_task
            except (asyncio.CancelledError, Exception):
                pass

        logger.info("[Session] Cleaned up")


async def handle_bot_connection(websocket) -> None:
    """Handle an incoming WebSocket connection from a vexa-bot."""
    remote = websocket.remote_address
    logger.info(f"[Server] Bot connected from {remote}")

    session = BotSession(websocket)

    try:
        # First message must be JSON config
        config_msg = await asyncio.wait_for(websocket.recv(), timeout=10)
        if isinstance(config_msg, bytes):
            logger.error("[Server] First message must be JSON config, got binary")
            return

        config = json.loads(config_msg)
        logger.info(f"[Server] Config received: meeting_id={config.get('meeting_id')}")

        # Send SERVER_READY
        await websocket.send(json.dumps({"status": "SERVER_READY"}))

        # Start Ultravox session
        await session.start(config)

        # Main message loop
        async for message in websocket:
            keep_going = await session.handle_bot_message(message)
            if not keep_going:
                break

    except asyncio.TimeoutError:
        logger.error("[Server] Timed out waiting for config")
    except websockets.ConnectionClosed as e:
        logger.info(f"[Server] Bot disconnected: code={e.code}")
    except Exception as e:
        logger.error(f"[Server] Error: {e}", exc_info=True)
    finally:
        await session.cleanup()
        logger.info(f"[Server] Bot session ended for {remote}")


# --- Health Check HTTP Server ---

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            status = 200 if is_healthy else 503
            body = json.dumps({"status": "ok" if is_healthy else "starting"}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def start_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"[Health] HTTP health check on port {HEALTH_PORT}")


# --- Main ---

async def main():
    global is_healthy

    start_health_server()

    logger.info(f"[Server] Starting Ultravox Agent service on port {WS_PORT}")
    async with websockets.serve(
        handle_bot_connection,
        "0.0.0.0",
        WS_PORT,
        max_size=2**20,  # 1MB max message
    ):
        is_healthy = True
        logger.info(f"[Server] Listening on ws://0.0.0.0:{WS_PORT}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
