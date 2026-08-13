"""Composition root — replaces the bridge's ``bot.py`` control plane.

Boots from ``VEXA_BOT_CONFIG``, joins the Discord voice channel named by the invocation's
``nativeMeetingId`` (no slash commands: like every other meeting-bot kind, this container is
spawned already knowing which meeting to join), receives per-speaker audio over the DAVE/E2EE
voice-gateway path, and reports through lifecycle.v1 / transcript.v1 / acts.v1.

Wires the real py-cord client, ``DAVEVoiceClient``, redis, and httpx into a ``MeetingSession`` (the
tested orchestration core, ``session.py``). Most of this module is NOT part of the offline test
suite, since it needs a live Discord gateway connection and a real Discord Application bot token,
which cannot be faked meaningfully offline; ``session.py`` carries the logic this module drives,
unit tested there with fakes. The two exceptions are ``_on_connect_task_done`` and the login-failure
lifecycle-emit path, both plain asyncio/dict logic with no discord.py dependency, unit tested in
``tests/test_bot.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any, Callable, Optional

import discord
import httpx
import redis.asyncio as redis_asyncio

from discord_bot.adapters.acts_redis import ActsSource, RedisPubSubClient
from discord_bot.adapters.lifecycle_http import LifecycleSink
from discord_bot.adapters.transcribe_http import transcribe as transcribe_http
from discord_bot.adapters.transcript_redis import TranscriptSink
from discord_bot.dave_voice.discord_protocol import DAVEVoiceProtocol
from discord_bot.dave_voice.voice_client import DAVEVoiceClient
from discord_bot.invocation import InvocationError, load_invocation
from discord_bot.session import MeetingSession, failed_event

# libdave (via dave.py) logs per-frame decrypt failures through the "dave" logger. A chunk of
# frames legitimately fail at stream start / silence / epoch edges — the successful frames are
# plenty for accurate transcripts, so silence the noise (bot.py precedent).
logging.getLogger("dave").setLevel(logging.CRITICAL)

FLUSH_POLL_S = 0.2  # matches the bridge's flusher tick
ALONE_POLL_S = 5.0  # channel-roster poll for leave-on-empty-channel


def _log(message: str) -> None:
    print(f"[discord-bot] {message}", flush=True)


async def _http_post(url: str, headers: dict, body: str) -> tuple[bool, int]:
    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, content=body, timeout=10.0)
        return res.is_success, res.status_code


async def _transcribe_post(url: str, *, data: dict, files: dict, timeout: float) -> Any:
    async with httpx.AsyncClient() as client:
        return await client.post(url, data=data, files=files, timeout=timeout)


async def _no_callback_post(url: str, headers: dict, body: str) -> tuple[bool, int]:
    raise RuntimeError("invocation.v1: meetingApiCallbackUrl not set — nowhere to POST lifecycle.v1 events")


async def _name_for(guild: discord.Guild, cache: dict[int, str], user_id: int) -> str:
    if user_id in cache:
        return cache[user_id]
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:  # noqa: BLE001 — a lookup failure falls back to the raw id, never fatal
            member = None
    name = member.display_name if member else str(user_id)
    cache[user_id] = name
    return name


def _non_bot_member_count(channel: discord.VoiceChannel) -> int:
    return sum(1 for m in channel.members if not m.bot)


def _on_connect_task_done(task: asyncio.Task[None], *, stop_event: asyncio.Event, log: Callable[[str], None] = _log) -> None:
    """``connect_task`` done-callback: if the gateway task drops or ``client.connect()`` raises
    after join, the coroutine would otherwise die silently and ``run()`` would wait on
    ``stop_event`` forever. Set it here so a dead gateway always unblocks shutdown."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log(f"gateway connect task ended with an exception: {exc!r}")
    stop_event.set()


async def run(env: Optional[dict[str, str]] = None) -> int:
    """Boot, join, run until stopped, report. Returns the process exit code."""
    env = os.environ if env is None else env
    try:
        inv = load_invocation(env)
    except InvocationError as e:
        _log(f"FATAL {e}")
        return 1

    discord_token = env.get("DISCORD_TOKEN")
    if not discord_token:
        _log("FATAL DISCORD_TOKEN is not set (forwarded by the discord-bot runtime profile)")
        return 1
    native_meeting_id = inv.get("nativeMeetingId")
    if not native_meeting_id:
        _log("FATAL invocation.v1: nativeMeetingId is required (the Discord voice channel id)")
        return 1

    redis_client = redis_asyncio.from_url(inv["redisUrl"])
    meeting_id = inv.get("meeting_id") or native_meeting_id or inv.get("connectionId") or "session"
    transcript = TranscriptSink(redis_client, meeting_id, native_meeting_id=native_meeting_id)

    callback_url = inv.get("meetingApiCallbackUrl")
    if callback_url:
        lifecycle = LifecycleSink(callback_url, internal_secret=inv.get("internalSecret"), post=_http_post, log=_log)
    else:
        lifecycle = LifecycleSink("", post=_no_callback_post, log=_log)

    transcription_url = inv.get("transcriptionServiceUrl")
    transcription_token = inv.get("transcriptionServiceToken")

    async def transcribe_fn(wav: bytes, language: Optional[str]) -> Optional[str]:
        if not transcription_url:
            return None
        return await transcribe_http(
            wav, url=transcription_url, token=transcription_token, language=language,
            post=_transcribe_post, log=_log,
        )

    name_cache: dict[int, str] = {}

    intents = discord.Intents.default()
    intents.voice_states = True
    client = discord.Client(intents=intents)

    session: Optional[MeetingSession] = None
    voice_protocol: Optional[DAVEVoiceProtocol] = None
    dave_client: Optional[DAVEVoiceClient] = None
    stop_event = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        nonlocal session, voice_protocol, dave_client
        session = MeetingSession(
            invocation=inv, lifecycle=lifecycle, transcript=transcript,
            transcribe=transcribe_fn, name_for=lambda uid: _name_for(channel.guild, name_cache, uid),
        )
        session.on_stop_requested(lambda: stop_event.set())
        await session.emit_joining()

        if not discord.opus.is_loaded():
            for lib in ("libopus.so.0", "libopus.so", "opus"):
                try:
                    discord.opus.load_opus(lib)
                    break
                except Exception:  # noqa: BLE001 — try the next candidate name
                    continue

        try:
            channel = await client.fetch_channel(int(native_meeting_id))
        except Exception as e:  # noqa: BLE001
            await session.emit_failed(failure_stage="joining", completion_reason="join_failure", reason=str(e))
            stop_event.set()
            return

        try:
            voice_protocol = await channel.connect(cls=DAVEVoiceProtocol, timeout=20)
        except Exception as e:  # noqa: BLE001
            await session.emit_failed(failure_stage="joining", completion_reason="join_failure", reason=str(e))
            stop_event.set()
            return
        if not (voice_protocol.token and voice_protocol.endpoint and voice_protocol.session_id):
            await voice_protocol.disconnect(force=True)
            await session.emit_failed(
                failure_stage="joining", completion_reason="join_failure",
                reason="incomplete voice handshake credentials",
            )
            stop_event.set()
            return

        dave_client = DAVEVoiceClient(
            server_id=channel.guild.id, channel_id=channel.id, user_id=client.user.id,
            session_id=voice_protocol.session_id, token=voice_protocol.token, endpoint=voice_protocol.endpoint,
            on_pcm=session.on_pcm,
        )
        try:
            await dave_client.start()
        except Exception as e:  # noqa: BLE001
            await voice_protocol.disconnect(force=True)
            await session.emit_failed(failure_stage="joining", completion_reason="join_failure", reason=str(e))
            stop_event.set()
            return

        acts_client = RedisPubSubClient(redis_client)
        acts = ActsSource(acts_client, meeting_id, log=_log)
        await acts.subscribe(session.handle_act)

        await session.emit_active()
        asyncio.create_task(_flush_loop(session, stop_event))
        asyncio.create_task(_alone_loop(session, channel, stop_event))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # signal handlers are POSIX-only; the runtime kernel's stop still lands via acts.v1 leave

    try:
        await client.login(discord_token)
    except Exception as e:  # noqa: BLE001 (no MeetingSession yet, so emit failed directly; unlike every
        # join-failure branch above, which calls session.emit_failed once session exists)
        await lifecycle.emit(
            failed_event(
                inv.get("connectionId") or "", failure_stage="requested", completion_reason="join_failure",
                reason=str(e),
            )
        )
        await redis_client.aclose()
        return 1
    connect_task = asyncio.create_task(client.connect())
    connect_task.add_done_callback(lambda t: _on_connect_task_done(t, stop_event=stop_event, log=_log))
    await stop_event.wait()

    if session is not None:
        await session.flush_all()
        completion_reason = session.stop_reason or "stopped"
        await session.emit_completed(completion_reason)
    if dave_client is not None:
        await dave_client.stop()
    if voice_protocol is not None:
        await voice_protocol.disconnect(force=True)
    await client.close()
    connect_task.cancel()
    await redis_client.aclose()
    return 0


async def _flush_loop(session: MeetingSession, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=FLUSH_POLL_S)
        except asyncio.TimeoutError:
            pass
        await session.flush_ready()


async def _alone_loop(session: MeetingSession, channel: discord.VoiceChannel, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=ALONE_POLL_S)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        if session.observe_channel_members(_non_bot_member_count(channel)):
            session.request_stop("left_alone")
