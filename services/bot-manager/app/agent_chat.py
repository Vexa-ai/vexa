"""
Agent chat manager — Quorum-style Claude CLI streaming inside bot containers.

Uses aiodocker to exec Claude CLI inside vexa-bot:experiment containers,
parses stream-json output, and yields SSE events back to the caller.
"""

import asyncio
import json
import logging
import struct
from typing import AsyncGenerator, Optional

import aiodocker

logger = logging.getLogger("bot_manager.agent_chat")


class AgentChatManager:
    """Manages Claude CLI sessions inside agent-enabled bot containers."""

    def __init__(self):
        self._docker: Optional[aiodocker.Docker] = None
        # Track active exec IDs per container for interrupt
        self._active: dict[str, str] = {}  # container_id -> exec_id

    async def _get_docker(self) -> aiodocker.Docker:
        if self._docker is None:
            self._docker = aiodocker.Docker(url="unix:///var/run/docker.sock")
        return self._docker

    async def chat(
        self,
        container_id: str,
        message: str,
        model: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Send a message to Claude inside the container, yield SSE events.
        Interrupts any existing response for this container first.
        """
        await self.interrupt(container_id)

        docker = await self._get_docker()

        # Read existing session_id from container
        session_id = await self._exec_simple(container_id,
            ["cat", "/app/vexa-bot/core/.claude/.session"])

        # Write prompt to file inside container (avoid shell escaping)
        await self._exec_with_stdin(container_id, message)

        # Build claude command
        claude_parts = [
            "claude",
            "--verbose", "--output-format", "stream-json",
        ]
        if session_id:
            claude_parts.extend(["--resume", session_id])
        if model:
            claude_parts.extend(["--model", model])
        claude_parts.extend(["-p", "$(cat /tmp/.chat-prompt.txt)"])

        cmd = ["sh", "-c", " ".join(claude_parts)]

        logger.info(f"Starting claude in container {container_id[:12]}... (session={session_id or 'new'})")

        # Create exec instance
        container = docker.containers.container(container_id)
        exec_obj = await container.exec(
            cmd=cmd,
            stdout=True,
            stderr=True,
            stdin=False,
            tty=False,
            workdir="/app/vexa-bot/core",
        )
        self._active[container_id] = exec_obj._id

        new_session_id = None
        try:
            # Start exec and stream output
            stream = exec_obj.start(detach=False)
            buffer = b""
            async for data in stream:
                if isinstance(data, bytes):
                    raw = data
                else:
                    raw = data.encode() if isinstance(data, str) else b""

                buffer += raw
                # Process complete lines
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        # Might be stderr or non-JSON output, skip
                        continue

                    for event in self._process_event(parsed):
                        if event.get("type") == "done" and event.get("session_id"):
                            new_session_id = event["session_id"]
                        yield event

            # Process remaining buffer
            if buffer.strip():
                try:
                    parsed = json.loads(buffer.strip())
                    for event in self._process_event(parsed):
                        if event.get("type") == "done" and event.get("session_id"):
                            new_session_id = event["session_id"]
                        yield event
                except json.JSONDecodeError:
                    pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
        finally:
            self._active.pop(container_id, None)

            # Check exec exit code
            try:
                inspect = await exec_obj.inspect()
                exit_code = inspect.get("ExitCode")
                if exit_code and exit_code != 0:
                    logger.warning(f"Claude exec exited with code {exit_code}")
            except Exception:
                pass

            # Save new session_id
            if new_session_id:
                await self._exec_simple(container_id, [
                    "sh", "-c",
                    f"mkdir -p /app/vexa-bot/core/.claude && echo '{new_session_id}' > /app/vexa-bot/core/.claude/.session"
                ])
                logger.info(f"Saved session {new_session_id[:12]}... for {container_id[:12]}")

    async def interrupt(self, container_id: str):
        """Kill active Claude process in container by sending kill to the exec PID."""
        exec_id = self._active.pop(container_id, None)
        if not exec_id:
            return
        logger.info(f"Interrupting exec {exec_id[:12]} in {container_id[:12]}")
        try:
            docker = await self._get_docker()
            # Find the PID of the exec and kill it inside the container
            # Use a broad kill: find claude processes and kill them
            container = docker.containers.container(container_id)
            await container.exec(
                cmd=["sh", "-c", "pkill -f 'claude.*stream-json' || true"],
                stdout=False, stderr=False, stdin=False, tty=False,
            )
        except Exception as e:
            logger.warning(f"Interrupt failed: {e}")

    async def reset_session(self, container_id: str):
        """Delete .claude/.session inside container to start fresh."""
        await self.interrupt(container_id)
        await self._exec_simple(container_id, [
            "rm", "-f", "/app/vexa-bot/core/.claude/.session"
        ])
        logger.info(f"Session reset for {container_id[:12]}")

    # --- Internal helpers ---

    async def _exec_simple(self, container_id: str, cmd: list[str]) -> Optional[str]:
        """Run a command in the container, return stdout or None."""
        try:
            docker = await self._get_docker()
            container = docker.containers.container(container_id)
            exec_obj = await container.exec(
                cmd=cmd,
                stdout=True, stderr=False, stdin=False, tty=False,
            )
            output = b""
            stream = exec_obj.start(detach=False)
            async for data in stream:
                if isinstance(data, bytes):
                    output += data
                elif isinstance(data, str):
                    output += data.encode()

            inspect = await exec_obj.inspect()
            if inspect.get("ExitCode", 1) == 0 and output.strip():
                return output.decode(errors="replace").strip()
        except Exception as e:
            logger.debug(f"exec_simple failed: {e}")
        return None

    async def _exec_with_stdin(self, container_id: str, content: str):
        """Write content to /tmp/.chat-prompt.txt inside the container."""
        # Use a simple approach: base64 encode to avoid escaping issues
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        await self._exec_simple(container_id, [
            "sh", "-c", f"echo '{encoded}' | base64 -d > /tmp/.chat-prompt.txt"
        ])

    def _process_event(self, data: dict) -> list[dict]:
        """Convert a Claude stream-json event into our SSE events."""
        events = []
        msg_type = data.get("type", "")

        if msg_type == "assistant":
            # Full or partial assistant message with content blocks
            content = data.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "text":
                    events.append({
                        "type": "text_delta",
                        "text": block["text"],
                    })
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    summary = self._summarize_tool(tool_name, tool_input)
                    events.append({
                        "type": "tool_use",
                        "tool": tool_name,
                        "summary": summary,
                    })

        elif msg_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                events.append({
                    "type": "text_delta",
                    "text": delta.get("text", ""),
                })

        elif msg_type == "result":
            session_id = data.get("session_id")
            events.append({
                "type": "done",
                "session_id": session_id,
                "cost_usd": data.get("cost_usd"),
                "duration_ms": data.get("duration_ms"),
            })

        return events

    def _summarize_tool(self, name: str, inp: dict) -> str:
        """Short human-readable summary of a tool invocation."""
        if name == "Read":
            return f"Reading: {inp.get('file_path', '?')}"
        elif name == "Write":
            return f"Writing: {inp.get('file_path', '?')}"
        elif name == "Edit":
            return f"Editing: {inp.get('file_path', '?')}"
        elif name in ("Glob", "Grep"):
            return f"{name}: {inp.get('pattern', '?')}"
        elif name == "Bash":
            cmd = inp.get("command", "?")
            return f"Running: {cmd[:60]}"
        elif name == "WebSearch":
            return f"Searching: {inp.get('query', '?')}"
        elif name == "WebFetch":
            return f"Fetching: {inp.get('url', '?')}"
        return f"{name}"


# Singleton
agent_chat_manager = AgentChatManager()
