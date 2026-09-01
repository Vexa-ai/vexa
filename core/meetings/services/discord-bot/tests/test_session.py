"""MeetingSession — the orchestration core (session.py), fully offline over injected ports:
lifecycle.v1 transitions, transcript.v1 segment emission (silence-gap → transcribe → publish),
acts.v1 handling, and leave-on-empty-channel (left_alone)."""

from discord_bot.contracts import conforms_lifecycle_event, conforms_transcript_segment
from discord_bot.session import MeetingSession


def _invocation(**overrides) -> dict:
    inv = {
        "platform": "discord",
        "meetingUrl": "https://discord.com/channels/1/2",
        "botName": "Vexa",
        "nativeMeetingId": "222",
        "redisUrl": "redis://redis:6379",
        "connectionId": "sess-uid-1",
    }
    inv.update(overrides)
    return inv


class FakeLifecycle:
    def __init__(self):
        self.events: list[dict] = []

    async def emit(self, event):
        self.events.append(event)


class FakeTranscript:
    def __init__(self):
        self.segments: list[dict] = []

    async def publish(self, segment):
        self.segments.append(segment)


def _session(*, invocation=None, transcribe_text="hello", clock=None, **kwargs) -> tuple[MeetingSession, FakeLifecycle, FakeTranscript]:
    lifecycle = FakeLifecycle()
    transcript = FakeTranscript()

    async def transcribe(wav, language):
        return transcribe_text

    async def name_for(uid):
        return f"user-{uid}"

    now = clock or (lambda: 0.0)
    session = MeetingSession(
        invocation=invocation or _invocation(), lifecycle=lifecycle, transcript=transcript,
        transcribe=transcribe, name_for=name_for, now=now, **kwargs,
    )
    return session, lifecycle, transcript


# ── lifecycle.v1 transitions ──────────────────────────────────────────────────────────────────


async def test_emit_joining_then_active():
    session, lifecycle, _ = _session()
    await session.emit_joining()
    await session.emit_active()
    assert [e["status"] for e in lifecycle.events] == ["joining", "active"]
    for e in lifecycle.events:
        assert e["connection_id"] == "sess-uid-1"
        conforms_lifecycle_event(e)


async def test_emit_failed_carries_stage_and_reason():
    session, lifecycle, _ = _session()
    await session.emit_failed(failure_stage="joining", completion_reason="join_failure", reason="timed out")
    event = lifecycle.events[-1]
    assert event["status"] == "failed"
    assert event["failure_stage"] == "joining"
    assert event["completion_reason"] == "join_failure"
    assert event["reason"] == "timed out"
    conforms_lifecycle_event(event)


async def test_emit_completed_carries_completion_reason():
    session, lifecycle, _ = _session()
    await session.emit_completed("left_alone")
    event = lifecycle.events[-1]
    assert event["status"] == "completed"
    assert event["completion_reason"] == "left_alone"
    conforms_lifecycle_event(event)


# ── segmenter → transcribe → transcript.v1 emission ───────────────────────────────────────────


def _pcm_seconds(seconds: float) -> bytes:
    from discord_bot.audio import BYTES_PER_SEC

    return b"\x00\x00" * int(seconds * BYTES_PER_SEC // 2)


async def test_flush_ready_emits_a_conforming_transcript_segment():
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0])
    session.on_pcm(42, _pcm_seconds(1.0))
    t[0] = session._silence_s - 0.1  # not silent long enough yet — still buffered
    await session.flush_ready()
    assert transcript.segments == []

    t[0] = session._silence_s + 0.1  # now past the silence gap
    await session.flush_ready()
    assert len(transcript.segments) == 1
    seg = transcript.segments[0]
    conforms_transcript_segment(seg)
    assert seg["text"] == "hello"
    assert seg["speaker"] == "user-42"
    assert seg["speaker_key"] == "discord:42"
    assert seg["completed"] is True


async def test_flush_ready_stamps_absolute_times():
    """Live pub/sub ingest passes segments straight through with no backfill (only the REST read
    path backfills absolute times), and the shared renderer drops any segment without
    absolute_start_time, so a segment missing this field never appears in a live meeting until a
    reload. Mirrors the TS parity reference's toBotSegment producer chokepoint."""
    from datetime import datetime

    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0])
    session.on_pcm(42, _pcm_seconds(1.0))
    t[0] = session._silence_s + 0.1
    await session.flush_ready()
    seg = transcript.segments[0]

    assert "absolute_start_time" in seg
    assert "absolute_end_time" in seg
    start = datetime.fromisoformat(seg["absolute_start_time"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(seg["absolute_end_time"].replace("Z", "+00:00"))
    assert end >= start


async def test_flush_ready_drops_blips_shorter_than_min_utterance():
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0], min_utterance_ms=1000)
    session.on_pcm(1, _pcm_seconds(0.05))  # well under 1000ms
    t[0] = 10.0
    await session.flush_ready()
    assert transcript.segments == []


async def test_flush_ready_skips_empty_transcription_result():
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0], transcribe_text="")
    session.on_pcm(1, _pcm_seconds(1.0))
    t[0] = 10.0
    await session.flush_ready()
    assert transcript.segments == []  # VAD-stripped silence — legitimate, not an error


async def test_flush_ready_skips_when_transcribe_returns_none():
    """None means the worker is unreachable — the caller must not emit a phantom segment."""
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0], transcribe_text=None)
    session.on_pcm(1, _pcm_seconds(1.0))
    t[0] = 10.0
    await session.flush_ready()
    assert transcript.segments == []


async def test_flush_all_ignores_silence_window():
    session, _, transcript = _session()
    session.on_pcm(7, _pcm_seconds(1.0))
    await session.flush_all()
    assert len(transcript.segments) == 1


async def test_segment_language_rides_the_invocation_language():
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0], invocation=_invocation(language="es"))
    session.on_pcm(1, _pcm_seconds(1.0))
    t[0] = 10.0
    await session.flush_ready()
    assert transcript.segments[0]["language"] == "es"


# ── acts.v1 handling ───────────────────────────────────────────────────────────────────────────


async def test_leave_act_requests_stop():
    session, _, _ = _session()
    stopped = []
    session.on_stop_requested(lambda: stopped.append(True))
    await session.handle_act({"action": "leave"})
    assert session.stopping is True
    assert session.stop_reason == "stopped"
    assert stopped == [True]


async def test_reconfigure_act_updates_language():
    t = [0.0]
    session, _, transcript = _session(clock=lambda: t[0])
    await session.handle_act({"action": "reconfigure", "language": "fr"})
    session.on_pcm(1, _pcm_seconds(1.0))
    t[0] = 10.0
    await session.flush_ready()
    assert transcript.segments[0]["language"] == "fr"


async def test_unrelated_act_is_a_harmless_no_op():
    session, _, _ = _session()
    await session.handle_act({"action": "speak_stop"})
    assert session.stopping is False


async def test_request_stop_is_idempotent():
    session, _, _ = _session()
    calls = []
    session.on_stop_requested(lambda: calls.append(True))
    session.request_stop("stopped")
    session.request_stop("left_alone")  # second call must not override or re-fire
    assert session.stop_reason == "stopped"
    assert calls == [True]


# ── left_alone (leave-on-empty-channel) ───────────────────────────────────────────────────────


async def test_observe_channel_members_present_never_trips():
    session, _, _ = _session(invocation=_invocation(automaticLeave={"everyoneLeftTimeout": 1000}))
    assert session.observe_channel_members(2) is False
    assert session.observe_channel_members(1) is False


async def test_observe_channel_members_trips_after_timeout():
    t = [0.0]
    session, _, _ = _session(clock=lambda: t[0], invocation=_invocation(automaticLeave={"everyoneLeftTimeout": 1000}))
    assert session.observe_channel_members(0) is False  # just went alone
    t[0] = 0.5
    assert session.observe_channel_members(0) is False  # not yet 1s
    t[0] = 1.1
    assert session.observe_channel_members(0) is True  # tripped


async def test_observe_channel_members_resets_when_someone_returns():
    t = [0.0]
    session, _, _ = _session(clock=lambda: t[0], invocation=_invocation(automaticLeave={"everyoneLeftTimeout": 1000}))
    session.observe_channel_members(0)
    t[0] = 0.9
    session.observe_channel_members(1)  # someone rejoins — resets the clock
    t[0] = 1.5
    assert session.observe_channel_members(0) is False  # only 0s alone again since the reset


async def test_observe_channel_members_defaults_to_invocation_automatic_leave():
    t = [0.0]
    session, _, _ = _session(clock=lambda: t[0], invocation=_invocation(automaticLeave={"everyoneLeftTimeout": 500}))
    session.observe_channel_members(0)
    t[0] = 0.6
    assert session.observe_channel_members(0) is True


async def test_observe_channel_members_explicit_override_wins_over_invocation():
    t = [0.0]
    session, _, _ = _session(
        clock=lambda: t[0], invocation=_invocation(automaticLeave={"everyoneLeftTimeout": 999_000}),
        everyone_left_timeout_ms=100,
    )
    session.observe_channel_members(0)
    t[0] = 0.2
    assert session.observe_channel_members(0) is True
