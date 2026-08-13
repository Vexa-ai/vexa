"""The mock composition root (Lane A) — swaps ONLY the Discord-specific ports (voice join + PCM
receive + transcription result) for canned fakes; every OTHER port is the REAL adapter
(``discord_bot.adapters.*``), driving the SAME real ``discord_bot.session.MeetingSession``
orchestrator the real bot (``discord_bot.bot``) uses.

So this proves the control plane end-to-end — REAL lifecycle.v1 HTTP callback, REAL transcript.v1
redis stream + pub/sub, REAL acts.v1 redis subscribe — with no live Discord gateway, no DAVE
handshake, no GPU/STT worker. Scenario selectable via env ``MOCK_SCENARIO`` (default ``normal``).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx
import redis.asyncio as redis_asyncio

from discord_bot.adapters.acts_redis import ActsSource, RedisPubSubClient
from discord_bot.adapters.lifecycle_http import LifecycleSink
from discord_bot.adapters.transcript_redis import TranscriptSink
from discord_bot.invocation import InvocationError, load_invocation
from discord_bot.session import MeetingSession


def _log(message: str) -> None:
    print(message, flush=True)

#: A canned mock scenario: how many synthetic utterances to speak, and from which fake user ids.
SCENARIOS: dict[str, dict[str, Any]] = {
    "normal": {"segments": 3, "user_ids": [111, 222]},
    "silence-left-alone": {"segments": 0, "user_ids": []},
    "immediate-stop": {"segments": 1, "user_ids": [111]},
}


async def _real_http_post(url: str, headers: dict, body: str) -> tuple[bool, int]:
    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, content=body, timeout=10.0)
        return res.is_success, res.status_code


def _canned_pcm(seconds: float = 0.3) -> bytes:
    from discord_bot.audio import BYTES_PER_SEC

    return b"\x00\x00" * int(seconds * BYTES_PER_SEC // 2)


async def run_mock(
    env: Optional[dict[str, str]] = None,
    *,
    redis_client: Optional[Any] = None,
    http_post=None,
    log=None,
) -> int:
    env = os.environ if env is None else env
    log = log or _log
    try:
        inv = load_invocation(env)
    except InvocationError as e:
        log(f"[mock] FATAL {e}")
        return 1

    scenario_name = env.get("MOCK_SCENARIO", "normal")
    scenario = SCENARIOS.get(scenario_name, SCENARIOS["normal"])

    redis_client = redis_client if redis_client is not None else redis_asyncio.from_url(inv["redisUrl"])
    meeting_id = inv.get("meeting_id") or inv.get("nativeMeetingId") or inv.get("connectionId") or "session"
    transcript = TranscriptSink(redis_client, meeting_id, native_meeting_id=inv.get("nativeMeetingId"))

    callback_url = inv.get("meetingApiCallbackUrl")
    post = http_post or _real_http_post
    lifecycle = (
        LifecycleSink(callback_url, internal_secret=inv.get("internalSecret"), post=post, log=log)
        if callback_url
        else LifecycleSink("", post=post, log=log)
    )

    seg_counter = {"n": 0}

    async def fake_transcribe(wav: bytes, language: Optional[str]) -> Optional[str]:
        seg_counter["n"] += 1
        return f"[mock utterance {seg_counter['n']}]"

    async def fake_name_for(uid: int) -> str:
        return f"mock-user-{uid}"

    session = MeetingSession(
        invocation=inv, lifecycle=lifecycle, transcript=transcript,
        transcribe=fake_transcribe, name_for=fake_name_for,
        silence_ms=50, min_utterance_ms=0,  # fast, deterministic — no real silence to wait out
    )
    stop_event = asyncio.Event()
    session.on_stop_requested(stop_event.set)

    acts_client = RedisPubSubClient(redis_client)
    acts = ActsSource(acts_client, meeting_id, log=log)
    await acts.subscribe(session.handle_act)

    await session.emit_joining()
    await session.emit_active()

    for uid in scenario["user_ids"]:
        for _ in range(scenario["segments"]):
            session.on_pcm(uid, _canned_pcm())
            await asyncio.sleep(0.06)  # cross the (short) silence gap so the next flush pops it
            await session.flush_ready()

    if scenario_name == "silence-left-alone":
        # Simulate the channel going empty immediately (everyoneLeftTimeout=0 for a fast mock run).
        while not stop_event.is_set():
            if session.observe_channel_members(0):
                session.request_stop("left_alone")
                break
            await asyncio.sleep(0.05)
    elif not stop_event.is_set():
        session.request_stop("stopped")

    await session.flush_all()
    await session.emit_completed(session.stop_reason or "stopped")
    if redis_client is not None:
        await redis_client.aclose()
    return 0


def main() -> int:
    return asyncio.run(run_mock())


if __name__ == "__main__":
    raise SystemExit(main())
