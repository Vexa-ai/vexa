"""
Agent chat manager — Quorum-style Claude CLI streaming inside bot containers.

Sends messages to Claude CLI running inside vexa-bot:experiment containers,
parses stream-json output, and yields SSE events back to the caller.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

logger = logging.getLogger("bot_manager.agent_chat")


class AgentChatManager:
    """Manages Claude CLI sessions inside agent-enabled bot containers."""

    def __init__(self):
        # Active subprocess per container_id
        self._active: dict[str, asyncio.subprocess.Process] = {}

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

        # Read existing session_id from container (if any)
        session_id = await self._read_session(container_id)

        # Write prompt to file inside container to avoid shell escaping issues
        await self._write_prompt(container_id, message)

        # Build claude command
        cmd = [
            "docker", "exec", container_id,
            "claude",
            "--verbose", "--output-format", "stream-json",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])
        if model:
            cmd.extend(["--model", model])

        cmd.extend(["-p", "$(cat /tmp/.chat-prompt.txt)"])

        # We need shell interpretation for the $(cat ...) substitution
        shell_cmd = " ".join(cmd)
        full_cmd = ["docker", "exec", container_id, "sh", "-c",
                     " ".join([
                         "claude",
                         "--verbose", "--output-format", "stream-json",
                         *(["--resume", session_id] if session_id else []),
                         *(["--model", model] if model else []),
                         "-p", "\"$(cat /tmp/.chat-prompt.txt)\"",
                     ])]

        logger.info(f"Starting claude in container {container_id[:12]}... (session={session_id or 'new'})")

        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active[container_id] = proc

        new_session_id = None
        try:
            async for event in self._parse_stream(proc.stdout):
                if event.get("type") == "done" and event.get("session_id"):
                    new_session_id = event["session_id"]
                yield event
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            self._active.pop(container_id, None)
            # Wait for process to finish
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()

            # Check for errors
            if proc.returncode and proc.returncode != 0:
                stderr = b""
                if proc.stderr:
                    try:
                        stderr = await asyncio.wait_for(proc.stderr.read(), timeout=2)
                    except (asyncio.TimeoutError, Exception):
                        pass
                if stderr:
                    err_text = stderr.decode(errors="replace").strip()
                    logger.warning(f"Claude stderr: {err_text[:500]}")
                    # Check for session errors
                    if "session" in err_text.lower() and ("expired" in err_text.lower() or "not found" in err_text.lower()):
                        await self._delete_session(container_id)
                        yield {"type": "error", "message": "Session expired, reset and retry"}
                        return

            # Save new session_id
            if new_session_id:
                await self._save_session(container_id, new_session_id)

    async def interrupt(self, container_id: str):
        """Kill active Claude process in container."""
        proc = self._active.pop(container_id, None)
        if proc and proc.returncode is None:
            logger.info(f"Interrupting active claude in {container_id[:12]}")
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def reset_session(self, container_id: str):
        """Delete .claude/.session inside container to start fresh."""
        await self.interrupt(container_id)
        await self._delete_session(container_id)
        logger.info(f"Session reset for {container_id[:12]}")

    # --- Internal helpers ---

    async def _write_prompt(self, container_id: str, message: str):
        """Write prompt to a temp file inside the container."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", container_id,
            "sh", "-c", "cat > /tmp/.chat-prompt.txt",
            stdin=asyncio.subprocess.PIPE,
        )
        await proc.communicate(message.encode())

    async def _read_session(self, container_id: str) -> Optional[str]:
        """Read session_id from .claude/.session inside container."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "cat", "/app/vexa-bot/core/.claude/.session",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            return stdout.decode().strip()
        return None

    async def _save_session(self, container_id: str, session_id: str):
        """Write session_id to .claude/.session inside container."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "sh", "-c", f"mkdir -p /app/vexa-bot/core/.claude && echo '{session_id}' > /app/vexa-bot/core/.claude/.session",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _delete_session(self, container_id: str):
        """Delete .claude/.session inside container."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "rm", "-f", "/app/vexa-bot/core/.claude/.session",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _parse_stream(self, stdout: asyncio.StreamReader) -> AsyncGenerator[dict, None]:
        """
        Parse Claude CLI --output-format stream-json output.

        Each line is a JSON object. We extract text deltas, tool use events,
        and the final result (which contains session_id).
        """
        buffer = b""
        while True:
            chunk = await stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for event in self._process_event(data):
                    yield event

        # Process any remaining buffer
        if buffer.strip():
            try:
                data = json.loads(buffer)
                for event in self._process_event(data):
                    yield event
            except json.JSONDecodeError:
                pass

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
            cost = data.get("cost_usd")
            events.append({
                "type": "done",
                "session_id": session_id,
                "cost_usd": cost,
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
