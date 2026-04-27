"""Vexa Telegram Bot — thin client on the Agent API.

Receives Telegram messages, auto-creates users via admin-api,
streams them through the Agent API, progressively edits responses.
Meeting commands proxy through api-gateway.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
import redis.asyncio as aioredis
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("vexa_tg_bot")

# --- Config ---

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AGENT_API_URL = os.getenv("AGENT_API_URL", os.getenv("CHAT_API_URL", "http://agent-api:8100"))
AGENT_API_TOKEN = os.getenv("AGENT_API_TOKEN", os.getenv("BOT_API_TOKEN", ""))
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "http://admin-api:8001")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://api-gateway:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

EDIT_INTERVAL = 1.0  # seconds between Telegram message edits
TOKEN_CACHE_TTL = 86400  # 24 hours — tokens auto-expire from Redis cache

# --- Redis connection ---

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # v0.10.5 Pack C.1 hardened client config (#267).
        _redis = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _redis


# --- Auth: Option B (auto-create, frictionless) ---


async def get_or_create_auth(tg_user) -> tuple[str, str]:
    """Return (user_id, api_token) for a Telegram user.

    Flow:
    1. Check Redis: telegram:{tg_id} -> user_id:token
    2. If missing: create user via admin-api, get token, store in Redis
    3. If user already exists (email collision): look up existing, create token
    """
    r = await get_redis()
    key = f"telegram:{tg_user.id}"

    cached = await r.get(key)
    if cached:
        user_id, token = cached.split(":", 1)
        return user_id, token

    # Auto-create via admin-api
    email = f"telegram_{tg_user.id}@telegram.user"
    name = tg_user.full_name or tg_user.username or f"tg_{tg_user.id}"

    async with httpx.AsyncClient(timeout=15) as client:
        # POST /admin/users — find-or-create
        resp = await client.post(
            f"{ADMIN_API_URL}/admin/users",
            headers={"X-Admin-API-Key": ADMIN_API_TOKEN},
            json={"email": email, "name": name},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create user: {resp.status_code} {resp.text[:200]}")

        user_data = resp.json()
        user_id = str(user_data["id"])

        # POST /admin/users/{id}/tokens — create API token
        token_resp = await client.post(
            f"{ADMIN_API_URL}/admin/users/{user_id}/tokens",
            headers={"X-Admin-API-Key": ADMIN_API_TOKEN},
        )
        if token_resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create token: {token_resp.status_code} {token_resp.text[:200]}")

        token = token_resp.json()["token"]

    # Store in Redis with TTL — forces periodic re-auth
    await r.set(key, f"{user_id}:{token}", ex=TOKEN_CACHE_TTL)
    logger.info(f"Auto-created auth for tg_user {tg_user.id} -> user {user_id}")
    return user_id, token


async def _invalidate_token(tg_user_id: int) -> None:
    """Clear cached token for a Telegram user, forcing re-auth on next call."""
    r = await get_redis()
    await r.delete(f"telegram:{tg_user_id}")
    logger.info(f"Invalidated cached token for tg_user {tg_user_id}")


# --- Tool labels ---

_TOOL_LABELS = {
    "Read": "Reading",
    "Write": "Writing",
    "Edit": "Editing",
    "Glob": "Finding files",
    "Grep": "Searching",
    "WebSearch": "Searching web",
    "WebFetch": "Fetching page",
    "Bash": "Running command",
}


def _format_activity(tool: str, summary: str) -> str:
    label = _TOOL_LABELS.get(tool, tool)
    if summary:
        short = summary[:50] + "\u2026" if len(summary) > 50 else summary
        return f"{label}: {short}"
    return f"{label}\u2026"


# --- Markdown to Telegram HTML ---


def _to_html(text: str) -> str:
    text = html.escape(text)
    # Code blocks
    text = re.sub(
        r"```(?:\w+)?\n(.*?)```",
        lambda m: f"<pre>{m.group(1)}</pre>",
        text, flags=re.DOTALL,
    )
    # Inline code
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    # Headers
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    # Italic
    text = re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _chunk_text(text: str, limit: int = 4000) -> list[str]:
    """Split text into chunks at paragraph boundaries, each <= limit chars."""
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Find a paragraph break near the limit
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            # Fall back to newline
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            # Fall back to space
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1:
            # Hard cut
            cut = limit

        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")

    return chunks


def _truncate(text: str, limit: int = 4000) -> str:
    """Telegram messages max 4096 chars. Truncate with indicator."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n<i>[truncated]</i>"


# --- Per-chat state ---


@dataclass
class ChatState:
    user_id: str
    tg_user_id: int = 0  # Telegram user ID for token refresh
    token: str = ""
    stream_task: asyncio.Task | None = None
    bot_msg_id: int | None = None
    accumulated: str = ""
    pending: str | None = None
    active_meeting: str | None = None  # "platform/native_id"
    meeting_aware_session_id: str | None = None  # session with meeting_aware=true


_states: dict[tuple[int, str], ChatState] = {}


def _get_state(chat_id: int, user_id: str, token: str = "", tg_user_id: int = 0) -> ChatState:
    key = (chat_id, user_id)
    if key not in _states:
        _states[key] = ChatState(user_id=user_id, tg_user_id=tg_user_id, token=token)
    else:
        if token:
            _states[key].token = token
            _states[key].user_id = user_id
        if tg_user_id:
            _states[key].tg_user_id = tg_user_id
    return _states[key]


# --- Keyboard helpers ---


def _kb_stop():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u23f9 Stop", callback_data="stop")]
    ])


# --- SSE streaming ---


async def _stream_response(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    state: ChatState,
    message: str,
    *,
    _retried: bool = False,
) -> None:
    bot = context.bot
    payload = {"user_id": state.user_id, "message": message, "bot_token": state.token}

    # Route through gateway when meeting is active (for auto meeting context injection)
    if state.meeting_aware_session_id:
        payload["session_id"] = state.meeting_aware_session_id
        chat_url = f"{GATEWAY_URL}/api/chat"
    else:
        chat_url = f"{AGENT_API_URL}/api/chat"

    state.accumulated = ""
    last_edit = 0.0
    current_activity = ""

    try:
        # Use user's token for gateway (it validates via admin-api), service token for direct agent-api
        if state.meeting_aware_session_id and state.token:
            _headers = {"X-API-Key": state.token}
        else:
            _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=None, headers=_headers) as client:
            async with client.stream("POST", chat_url, json=payload) as resp:
                if resp.status_code == 403 and not _retried and state.tg_user_id:
                    # Token revoked — invalidate cache, re-auth, retry once
                    await _invalidate_token(state.tg_user_id)
                    from types import SimpleNamespace
                    fake_user = SimpleNamespace(id=state.tg_user_id, full_name=None, username=None)
                    new_user_id, new_token = await get_or_create_auth(fake_user)
                    state.user_id = new_user_id
                    state.token = new_token
                    logger.info(f"Token refreshed for tg_user {state.tg_user_id} after 403")
                    return await _stream_response(chat_id, context, state, message, _retried=True)
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"API error {resp.status_code}: {body.decode()[:200]}")

                buf = b""
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    while b"\n" in buf:
                        raw_line, buf = buf.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        etype = event.get("type")

                        if etype == "text_delta":
                            state.accumulated += event.get("text", "")
                            current_activity = ""

                            now = asyncio.get_event_loop().time()
                            if now - last_edit >= EDIT_INTERVAL:
                                display = f"\u23f3 {state.accumulated}"
                                await _safe_edit(bot, chat_id, state, display)
                                last_edit = now

                        elif etype == "tool_use":
                            current_activity = _format_activity(
                                event.get("tool", ""), event.get("summary", "")
                            )
                            now = asyncio.get_event_loop().time()
                            if now - last_edit >= EDIT_INTERVAL:
                                prefix = state.accumulated + "\n\n" if state.accumulated else ""
                                display = f"\u23f3 {prefix}\u2699\ufe0f {current_activity}"
                                await _safe_edit(bot, chat_id, state, display)
                                last_edit = now

                        elif etype in ("done", "stream_end"):
                            break

                        elif etype == "error":
                            state.accumulated += f"\n\n\u26a0\ufe0f {event.get('message', 'Unknown error')}"
                            break

        # Final formatted message — chunk if needed
        final = state.accumulated or "(no response)"
        final_html = _to_html(final)
        chunks = _chunk_text(final_html)

        if len(chunks) == 1:
            await _safe_edit(bot, chat_id, state, _truncate(chunks[0]), parse_mode="HTML", markup=None)
        else:
            # First chunk edits the existing message
            await _safe_edit(bot, chat_id, state, _truncate(chunks[0]), parse_mode="HTML", markup=None)
            # Subsequent chunks as new messages
            for chunk in chunks[1:]:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=_truncate(chunk),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send chunk: {e}")

    except asyncio.CancelledError:
        partial = state.accumulated or "\u2026"
        partial_html = _truncate(_to_html(partial) + "\n\n<i>[stopped]</i>")
        await _safe_edit(bot, chat_id, state, partial_html, parse_mode="HTML", markup=None)
    except Exception as e:
        logger.error(f"Stream error for {state.user_id}: {e}", exc_info=True)
        await _safe_edit(bot, chat_id, state, f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML", markup=None)


async def _safe_edit(
    bot: Bot,
    chat_id: int,
    state: ChatState,
    text: str,
    parse_mode: str | None = None,
    markup=_kb_stop,
):
    """Edit or send message, swallowing transient errors."""
    if markup is _kb_stop:
        markup = _kb_stop()
    try:
        if state.bot_msg_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.bot_msg_id,
                text=text[:4096],
                parse_mode=parse_mode,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        else:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text[:4096],
                parse_mode=parse_mode,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            state.bot_msg_id = msg.message_id
    except Exception:
        pass


# --- Start stream with typing indicator ---


async def _start_stream(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    state: ChatState,
    message: str,
) -> None:
    bot = context.bot

    async def _typing():
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    thinking = await bot.send_message(chat_id=chat_id, text="\u23f3", reply_markup=_kb_stop())
    state.bot_msg_id = thinking.message_id

    typing_task = asyncio.create_task(_typing())

    async def _run():
        try:
            await _stream_response(chat_id, context, state, message)
        finally:
            typing_task.cancel()

    state.stream_task = asyncio.create_task(_run())


# --- Interrupt ---


async def _interrupt(state: ChatState):
    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=10, headers=_headers) as client:
            await client.request(
                "DELETE", f"{AGENT_API_URL}/api/chat",
                json={"user_id": state.user_id},
            )
    except Exception:
        pass
    if state.stream_task and not state.stream_task.done():
        state.stream_task.cancel()
        try:
            await state.stream_task
        except (asyncio.CancelledError, Exception):
            pass
    state.stream_task = None


# --- Auth helper for handlers ---


async def _ensure_auth(update: Update) -> tuple[ChatState, int]:
    """Get or create auth, return (state, chat_id). Raises on failure."""
    tg_user = update.effective_user
    chat_id = update.effective_chat.id
    user_id, token = await get_or_create_auth(tg_user)
    state = _get_state(chat_id, user_id, token, tg_user_id=tg_user.id)
    return state, chat_id


# --- Command handlers ---


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    await update.message.reply_text(
        f"Vexa Agent ready.\n\n"
        f"Your user ID: <code>{html.escape(state.user_id)}</code>\n\n"
        f"Send me a message and I'll forward it to your AI agent.\n\n"
        f"<b>Commands:</b>\n"
        f"/new \u2014 New agent session\n"
        f"/sessions \u2014 List sessions\n"
        f"/files \u2014 List workspace files\n"
        f"/join &lt;url&gt; \u2014 Join a meeting\n"
        f"/stop \u2014 Stop active meeting\n"
        f"/speak &lt;text&gt; \u2014 Speak in meeting\n"
        f"/transcript \u2014 Get meeting transcript\n"
        f"/reset \u2014 Reset session\n"
        f"/help \u2014 Show this help",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>Available commands:</b>\n\n"
        "<b>Chat</b>\n"
        "Send any text \u2014 Chat with your AI agent\n"
        "/new \u2014 Create a new agent session\n"
        "/sessions \u2014 List your sessions\n"
        "/files \u2014 List workspace files\n"
        "/reset \u2014 Reset current session (keeps files)\n\n"
        "<b>Meetings</b>\n"
        "/join &lt;meeting_url&gt; \u2014 Send a bot to join a meeting\n"
        "/stop \u2014 Stop the active meeting bot\n"
        "/speak &lt;text&gt; \u2014 Make the bot speak (TTS)\n"
        "/transcript \u2014 Get the latest transcript\n\n"
        "<b>Other</b>\n"
        "/help \u2014 Show this message",
        parse_mode="HTML",
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    await _interrupt(state)
    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=10, headers=_headers) as client:
            await client.post(
                f"{AGENT_API_URL}/api/chat/reset",
                json={"user_id": state.user_id},
            )
    except Exception:
        pass
    await update.message.reply_text("Session reset. Files in workspace kept.")


async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    # Get session name from args or default
    name = " ".join(context.args) if context.args else "New session"

    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=10, headers=_headers) as client:
            resp = await client.post(
                f"{AGENT_API_URL}/api/sessions",
                json={"user_id": state.user_id, "name": name},
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                sid = data.get("session_id", "?")
                await update.message.reply_text(
                    f"New session created: <code>{html.escape(name)}</code>\n"
                    f"ID: <code>{html.escape(sid)}</code>",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to create session: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=10, headers=_headers) as client:
            resp = await client.get(
                f"{AGENT_API_URL}/api/sessions",
                params={"user_id": state.user_id},
            )
            if resp.status_code == 200:
                sessions = resp.json().get("sessions", [])
                if not sessions:
                    await update.message.reply_text("No sessions found. Use /new to create one.")
                    return
                lines = ["<b>Your sessions:</b>\n"]
                for s in sessions:
                    name = html.escape(s.get("name", "Unnamed"))
                    sid = html.escape(s.get("session_id", s.get("id", "?")))
                    lines.append(f"\u2022 <code>{sid[:8]}</code> \u2014 {name}")
                await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to list sessions: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=10, headers=_headers) as client:
            resp = await client.get(
                f"{AGENT_API_URL}/api/workspace/files",
                params={"user_id": state.user_id},
            )
            if resp.status_code == 200:
                files = resp.json().get("files", [])
                if not files:
                    await update.message.reply_text("Workspace is empty.")
                    return
                file_list = "\n".join(files[:50])
                msg = f"<b>Workspace files:</b>\n<pre>{html.escape(file_list)}</pre>"
                if len(files) > 50:
                    msg += f"\n<i>... and {len(files) - 50} more</i>"
                await update.message.reply_text(msg, parse_mode="HTML")
            elif resp.status_code == 404:
                await update.message.reply_text("No active container. Send a message first to start your agent.")
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to list files: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


# --- Meeting commands ---


def _parse_meeting_url(url: str) -> tuple[str, str] | None:
    """Extract platform and native_meeting_id from a meeting URL.

    Supports:
    - Google Meet: https://meet.google.com/abc-defg-hij
    - Microsoft Teams: https://teams.microsoft.com/l/meetup-join/...
    - Zoom: https://zoom.us/j/123456789
    """
    if "meet.google.com" in url:
        match = re.search(r"meet\.google\.com/([a-z\-]+)", url)
        if match:
            return "google_meet", match.group(1)
    elif "teams.microsoft.com" in url or "teams.live.com" in url:
        # Teams URLs are complex — pass the full URL as native_id
        return "microsoft_teams", url
    elif "zoom.us" in url or "zoom.com" in url:
        match = re.search(r"/j/(\d+)", url)
        if match:
            return "zoom", match.group(1)
    return None


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not context.args:
        await update.message.reply_text("Usage: /join &lt;meeting_url&gt;", parse_mode="HTML")
        return

    meeting_url = context.args[0]
    parsed = _parse_meeting_url(meeting_url)

    try:
        # Use user's own token for gateway auth (gateway validates via admin-api)
        _headers = {"X-API-Key": state.token} if state.token else {}
        async with httpx.AsyncClient(timeout=30, headers=_headers) as client:
            body = {"meeting_url": meeting_url}
            if parsed:
                body["platform"] = parsed[0]
                body["native_meeting_id"] = parsed[1]

            resp = await client.post(f"{GATEWAY_URL}/bots", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                platform = data.get("platform", parsed[0] if parsed else "unknown")
                native_id = data.get("native_meeting_id", parsed[1] if parsed else "?")
                state.active_meeting = f"{platform}/{native_id}"

                # Create meeting-aware session so gateway injects meeting context
                try:
                    sess_resp = await client.post(
                        f"{GATEWAY_URL}/api/sessions",
                        json={"user_id": state.user_id, "name": f"Meeting: {native_id[:20]}", "meeting_aware": True},
                    )
                    if sess_resp.status_code in (200, 201):
                        state.meeting_aware_session_id = sess_resp.json().get("session_id")
                        logger.info(f"Meeting-aware session created: {state.meeting_aware_session_id}")
                except Exception as e:
                    logger.warning(f"Failed to create meeting-aware session: {e}")

                await update.message.reply_text(
                    f"Bot joining meeting.\n"
                    f"Platform: <code>{html.escape(platform)}</code>\n"
                    f"Meeting: <code>{html.escape(str(native_id)[:50])}</code>\n\n"
                    f"Use /stop to end, /speak to talk, /transcript to read.",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"\u26a0\ufe0f Failed to join: {resp.status_code}\n{html.escape(resp.text[:200])}",
                    parse_mode="HTML",
                )
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


async def stop_meeting_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not state.active_meeting:
        await update.message.reply_text("No active meeting. Use /join to start one.")
        return

    try:
        _headers = {"X-API-Key": state.token} if state.token else {}
        async with httpx.AsyncClient(timeout=15, headers=_headers) as client:
            resp = await client.delete(f"{GATEWAY_URL}/bots/{state.active_meeting}")
            meeting_ref = state.active_meeting
            state.active_meeting = None
            state.meeting_aware_session_id = None
            if resp.status_code in (200, 202, 204):
                await update.message.reply_text(f"Meeting bot stopped: <code>{html.escape(meeting_ref)}</code>", parse_mode="HTML")
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to stop: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


async def speak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not state.active_meeting:
        await update.message.reply_text("No active meeting. Use /join first.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /speak &lt;text to say&gt;", parse_mode="HTML")
        return

    text_to_speak = " ".join(context.args)

    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=30, headers=_headers) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/bots/{state.active_meeting}/speak",
                json={"text": text_to_speak},
            )
            if resp.status_code in (200, 202):
                await update.message.reply_text(f"\U0001f50a Speaking: \"{text_to_speak[:100]}\"")
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to speak: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


async def transcript_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not state.active_meeting:
        await update.message.reply_text("No active meeting. Use /join first.")
        return

    try:
        _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
        async with httpx.AsyncClient(timeout=15, headers=_headers) as client:
            resp = await client.get(f"{GATEWAY_URL}/transcripts/{state.active_meeting}")
            if resp.status_code == 200:
                data = resp.json()
                segments = data.get("segments", data.get("transcript", []))
                if not segments:
                    await update.message.reply_text("No transcript available yet.")
                    return

                lines = []
                for seg in segments[-30:]:  # Last 30 segments
                    speaker = seg.get("speaker", "Unknown")
                    text = seg.get("text", "")
                    lines.append(f"<b>{html.escape(speaker)}</b>: {html.escape(text)}")

                transcript_text = "\n".join(lines)
                chunks = _chunk_text(transcript_text)
                for chunk in chunks:
                    await update.message.reply_text(
                        _truncate(chunk),
                        parse_mode="HTML",
                    )
            else:
                await update.message.reply_text(f"\u26a0\ufe0f Failed to get transcript: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Error: {html.escape(str(e))}", parse_mode="HTML")


# --- Message handler ---


def _is_group_chat(update: Update) -> bool:
    """Check if the message is in a group/supergroup chat."""
    return update.effective_chat.type in ("group", "supergroup")


def _should_respond_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """In group chats, only respond when bot is @mentioned or message replies to bot."""
    msg = update.message
    # Reply to one of the bot's messages
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            return True
    # @mentioned — check entities for bot_username mention
    if msg.entities:
        bot_username = context.bot.username
        if bot_username:
            for entity in msg.entities:
                if entity.type == "mention":
                    mention = msg.text[entity.offset:entity.offset + entity.length]
                    if mention.lower() == f"@{bot_username.lower()}":
                        return True
    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    # Group chat: only respond when @mentioned or replied to
    if _is_group_chat(update):
        if not _should_respond_in_group(update, context):
            return

    text = update.message.text
    if not text or not text.strip():
        await update.message.reply_text("Send me a text message.")
        return
    text = text.strip()

    # Strip bot @mention from the message text in groups
    if _is_group_chat(update) and context.bot.username:
        text = re.sub(rf"@{re.escape(context.bot.username)}\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            await update.message.reply_text("Send me a text message after @mentioning me.")
            return

    try:
        state, chat_id = await _ensure_auth(update)
    except Exception as e:
        await update.message.reply_text(f"\u26a0\ufe0f Auth failed: {html.escape(str(e))}", parse_mode="HTML")
        return

    # If streaming: queue this message
    if state.stream_task and not state.stream_task.done():
        state.pending = text
        return

    await _start_stream(chat_id, context, state, text)


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    chat_id = query.message.chat_id
    try:
        state, _ = await _ensure_auth(update)
    except Exception:
        return

    if query.data == "stop":
        await _interrupt(state)

        pending = state.pending
        state.pending = None
        if pending:
            await _start_stream(chat_id, context, state, pending)


# --- Internal trigger API (for scheduler) ---

from fastapi import FastAPI as TriggerFastAPI
import uvicorn

trigger_app = TriggerFastAPI()

# Global ref to telegram app
_tg_app: Application | None = None

TRIGGER_PORT = int(os.getenv("TELEGRAM_BOT_PORT", "8200"))


def _resolve_chat_id(user_id: str) -> int | None:
    """Find the Telegram chat_id for a given user_id."""
    for (chat_id, uid), state in _states.items():
        if state.user_id == user_id:
            return chat_id
    return None


def _resolve_state(user_id: str) -> ChatState | None:
    """Find the ChatState for a given user_id."""
    for (chat_id, uid), state in _states.items():
        if state.user_id == user_id:
            return state
    return None


@trigger_app.post("/internal/trigger")
async def trigger_chat(request: dict):
    """Receive a scheduled trigger and start a chat turn for the user."""
    user_id = request.get("user_id")
    message = request.get("message", "Scheduled reminder")

    if not user_id or not _tg_app:
        return {"status": "error", "detail": "bot not ready or missing user_id"}

    chat_id = _resolve_chat_id(user_id)
    if not chat_id:
        logger.warning(f"Trigger: no chat_id for user {user_id}")
        return {"status": "error", "detail": f"no chat_id for user {user_id}"}

    state = _resolve_state(user_id)
    if not state:
        return {"status": "error", "detail": f"no state for user {user_id}"}

    # If already streaming, queue it
    if state.stream_task and not state.stream_task.done():
        state.pending = message
        return {"status": "queued"}

    await _start_stream_triggered(chat_id, _tg_app.bot, state, message)
    return {"status": "triggered"}


async def _start_stream_triggered(chat_id: int, bot: Bot, state: ChatState, message: str):
    """Start a chat stream from a trigger (no Update/context)."""

    class _FakeContext:
        def __init__(self, b):
            self.bot = b

    fake_ctx = _FakeContext(bot)

    async def _typing():
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    thinking = await bot.send_message(chat_id=chat_id, text=f"\U0001f514 {message[:100]}\n\n\u23f3")
    state.bot_msg_id = thinking.message_id

    typing_task = asyncio.create_task(_typing())

    async def _run():
        try:
            await _stream_response(chat_id, fake_ctx, state, message)
        finally:
            typing_task.cancel()

    state.stream_task = asyncio.create_task(_run())


@trigger_app.get("/health")
async def trigger_health():
    return {"status": "ok", "bot_ready": _tg_app is not None}


# --- Main ---


async def main() -> None:
    global _tg_app

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)

    tg_app = Application.builder().token(BOT_TOKEN).request(request).build()
    _tg_app = tg_app

    # Register command handlers
    tg_app.add_handler(CommandHandler("start", start_command))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("reset", reset_command))
    tg_app.add_handler(CommandHandler("new", new_session_command))
    tg_app.add_handler(CommandHandler("sessions", sessions_command))
    tg_app.add_handler(CommandHandler("files", files_command))
    tg_app.add_handler(CommandHandler("join", join_command))
    tg_app.add_handler(CommandHandler("stop", stop_meeting_command))
    tg_app.add_handler(CommandHandler("speak", speak_command))
    tg_app.add_handler(CommandHandler("transcript", transcript_command))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.add_handler(CallbackQueryHandler(handle_button))

    logger.info(f"Bot starting (Agent API: {AGENT_API_URL}, Gateway: {GATEWAY_URL})")

    async with tg_app:
        await tg_app.bot.set_my_commands([
            ("start", "Welcome + auth"),
            ("help", "Show available commands"),
            ("new", "Create new agent session"),
            ("sessions", "List your sessions"),
            ("files", "List workspace files"),
            ("join", "Join a meeting"),
            ("stop", "Stop active meeting"),
            ("speak", "Speak in meeting (TTS)"),
            ("transcript", "Get meeting transcript"),
            ("reset", "Reset session (keeps files)"),
        ])
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot is polling. Ctrl+C to stop.")

        # Start trigger API server in background
        config = uvicorn.Config(trigger_app, host="0.0.0.0", port=TRIGGER_PORT, log_level="info")
        server = uvicorn.Server(config)
        trigger_task = asyncio.create_task(server.serve())
        logger.info(f"Trigger API listening on port {TRIGGER_PORT}")

        # Verify Redis connection
        try:
            r = await get_redis()
            await r.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis not available: {e} — auth will fail until Redis is up")

        try:
            await asyncio.Event().wait()
        finally:
            server.should_exit = True
            await trigger_task
            await tg_app.updater.stop()
            await tg_app.stop()
            if _redis:
                await _redis.close()


if __name__ == "__main__":
    asyncio.run(main())
