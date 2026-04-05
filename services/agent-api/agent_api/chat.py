"""SSE chat streaming via container exec.

Routes user messages to an AI agent CLI running inside a container.
Streams the response back as Server-Sent Events.
"""

import base64
import json
import logging
import shlex
from typing import AsyncGenerator, Optional

from agent_api import config
from agent_api.container_manager import ContainerManager
from agent_api.stream_parser import parse_event
from agent_api import workspace

logger = logging.getLogger("agent_api.chat")

# Redis key prefixes for session state
SESSION_PREFIX = "agent:session:"
SESSIONS_INDEX = "agent:sessions:"


# --- Session helpers (Redis-backed) ---

async def get_session(redis, user_id: str, session_id: Optional[str] = None) -> Optional[str]:
    """Get agent CLI session ID from Redis."""
    if session_id:
        return session_id
    return await redis.get(f"{SESSION_PREFIX}{user_id}")


async def save_session(redis, user_id: str, session_id: str):
    """Save session ID to Redis with 7-day TTL."""
    await redis.set(f"{SESSION_PREFIX}{user_id}", session_id, ex=86400 * 7)


async def clear_session(redis, user_id: str):
    """Clear session ID from Redis."""
    await redis.delete(f"{SESSION_PREFIX}{user_id}")


async def list_sessions(redis, user_id: str) -> list[dict]:
    """List all sessions for a user from Redis index."""
    data = await redis.hgetall(f"{SESSIONS_INDEX}{user_id}")
    sessions = []
    for sid, meta_json in data.items():
        try:
            meta = json.loads(meta_json)
            meta["id"] = sid
            sessions.append(meta)
        except json.JSONDecodeError:
            sessions.append({"id": sid, "name": sid[:8]})
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


async def save_session_meta(redis, user_id: str, session_id: str, name: str, extra: dict = None):
    """Save/update session metadata in Redis index."""
    import time
    existing = await redis.hget(f"{SESSIONS_INDEX}{user_id}", session_id)
    meta = json.loads(existing) if existing else {"created_at": time.time()}
    meta["name"] = name
    meta["updated_at"] = time.time()
    if extra:
        meta.update(extra)
    await redis.hset(f"{SESSIONS_INDEX}{user_id}", session_id, json.dumps(meta))
    await redis.expire(f"{SESSIONS_INDEX}{user_id}", 86400 * 30)


async def get_session_meta(redis, user_id: str, session_id: str) -> Optional[dict]:
    """Get session metadata from Redis index."""
    raw = await redis.hget(f"{SESSIONS_INDEX}{user_id}", session_id)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return None


async def delete_session_meta(redis, user_id: str, session_id: str):
    """Remove a session from the index."""
    await redis.hdel(f"{SESSIONS_INDEX}{user_id}", session_id)


# --- Workspace init on new container ---


async def _workspace_init(cm: ContainerManager, user_id: str, container: str,
                          workspace_name: str = "default"):
    """Restore workspace from storage, init from template or git if empty.

    Called once when a new container is created (_new_container=True).
    Order: sync_down → if empty → git clone or template copy.
    Fail-safe: existing user with failed sync_down = abort (raise).
    """
    # 1. sync_down from MinIO
    had_workspace = await workspace.workspace_exists(user_id, workspace_name)
    sync_ok = await workspace.sync_down(user_id, container, workspace_name)

    if not sync_ok and had_workspace:
        # Existing user, sync failed — abort. Don't run agent with empty workspace.
        raise RuntimeError(
            f"sync_down failed for {user_id} but workspace exists in storage. "
            "Aborting to prevent data loss."
        )

    if not sync_ok:
        logger.warning(f"sync_down failed for {user_id} (first-time user), continuing with init")

    # 2. Check if workspace is still empty after sync
    if await workspace.is_workspace_empty(container):
        # Try git clone if configured (get_user_data is cached per user_id, no extra HTTP)
        user_data = await cm.get_user_data(user_id)
        git_config = user_data.get("workspace_git", {})
        if git_config and git_config.get("repo"):
            repo = git_config["repo"]
            branch = git_config.get("branch", "main")
            token = git_config.get("token", "")
            logger.info(f"Git clone init for {user_id}: {repo} ({branch})")
            clone_ok = await workspace.git_clone_init(container, repo, branch, token)
            if not clone_ok:
                raise RuntimeError(f"Git clone failed for {user_id} from {repo}")
        else:
            # Default: copy template
            logger.info(f"Template init for {user_id}")
            await workspace.init_from_template(container)

    logger.info(f"Workspace init complete for {user_id}")


# --- Core chat turn ---

async def run_chat_turn(
    redis,
    cm: ContainerManager,
    user_id: str,
    message: str,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    session_name: Optional[str] = None,
    context_prefix: str = "",
    cli_flags: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    """Run a single chat turn. Yields SSE data strings.

    Args:
        redis: Async Redis client.
        cm: Container manager instance.
        user_id: User identifier.
        message: User message text.
        model: Optional model override.
        session_id: Optional specific session to resume.
        session_name: Human-readable name for new sessions.
        context_prefix: Optional text prepended to the prompt (workspace context, etc).
        cli_flags: Optional extra flags forwarded verbatim to agent CLI.
    """
    cm._new_container = False
    effective_session = session_id or "default"
    container = await cm.ensure_container(user_id, session_id=effective_session)

    # Determine workspace name from session metadata
    workspace_name = "default"
    if session_id:
        meta = await get_session_meta(redis, user_id, session_id)
        if meta and meta.get("workspace"):
            workspace_name = meta["workspace"]

    # Store workspace_name on container info for periodic sync
    key = f"{user_id}:{effective_session}"
    if key in cm._containers:
        cm._containers[key].workspace_name = workspace_name

    # Workspace init on new container
    if cm._new_container:
        yield f"data: {json.dumps({'type': 'session_reset', 'reason': 'Container was recreated. Previous session context is no longer available.'})}\n\n"
        await _workspace_init(cm, user_id, container, workspace_name)

    # Session from Redis — skip if container was just recreated
    if not cm._new_container:
        session_id = await get_session(redis, user_id, session_id)
        if session_id:
            check = await cm.exec_simple(container, [
                "sh", "-c",
                f"test -f {config.AGENT_WORKSPACE_PATH}/{session_id}.jsonl && echo OK || echo MISSING",
            ])
            if check and "MISSING" in check:
                logger.warning(f"Session {session_id[:12]} not found in container, starting fresh")
                await clear_session(redis, user_id)
                session_id = None
    else:
        session_id = None

    # Build prompt (with optional context prefix)
    full_prompt = f"{context_prefix}\n\n---\n\n{message}" if context_prefix else message
    encoded = base64.b64encode(full_prompt.encode()).decode()
    await cm.exec_with_stdin(
        container,
        ["sh", "-c", "base64 -d > /tmp/.chat-prompt.txt"],
        stdin_data=encoded.encode(),
    )

    # Agent CLI command
    cli = config.AGENT_CLI
    allowed_tools = config.AGENT_ALLOWED_TOOLS
    parts = [
        cli,
        "--verbose", "--output-format", "stream-json",
        "--allowedTools", f"'{allowed_tools}'",
    ]
    if session_id:
        parts.extend(["--resume", session_id])
    if model or config.DEFAULT_MODEL:
        parts.extend(["--model", model or config.DEFAULT_MODEL])
    # Forward extra CLI flags (shell-escaped to prevent injection)
    if cli_flags:
        for flag in cli_flags:
            parts.append(shlex.quote(str(flag)))
    parts.extend(["-p", '"$(cat /tmp/.chat-prompt.txt)"'])

    workspace = config.WORKSPACE_PATH
    cmd = f"cd {workspace} && {' '.join(parts)}"

    logger.info(f"Chat for {user_id} (session={session_id or 'new'}, model={model or 'default'})")

    proc = await cm.exec_stream(container, cmd)
    new_session_id = None
    buffer = b""

    try:
        async for chunk in proc.stdout:
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for event in parse_event(parsed):
                    if event.get("type") == "done" and event.get("session_id"):
                        new_session_id = event["session_id"]
                    yield f"data: {json.dumps(event)}\n\n"

        # Remaining buffer
        if buffer.strip():
            try:
                parsed = json.loads(buffer.strip())
                for event in parse_event(parsed):
                    if event.get("type") == "done" and event.get("session_id"):
                        new_session_id = event["session_id"]
                    yield f"data: {json.dumps(event)}\n\n"
            except json.JSONDecodeError:
                pass
    finally:
        await proc.wait()

        # Save session in finally — runs even if generator is cancelled.
        # Workspace auto-save is handled by BackgroundTask in main.py.
        if new_session_id:
            await save_session(redis, user_id, new_session_id)
            await save_session_meta(
                redis, user_id, new_session_id,
                session_name or f"Session {new_session_id[:8]}",
            )
            logger.info(f"Session saved: {new_session_id[:12]}... for {user_id}")

    yield f"data: {json.dumps({'type': 'stream_end', 'session_id': new_session_id or session_id})}\n\n"
