"""Adapter-layer tests: transcript.v1 emission, lifecycle.v1 callbacks, acts.v1 ingress, all
offline, against fakes (no live redis/HTTP)."""

import json
from pathlib import Path

from discord_bot.adapters.acts_redis import ActsSource, acts_channel
from discord_bot.adapters.lifecycle_http import LifecycleSink
from discord_bot.adapters.transcript_redis import TRANSCRIPTION_STREAM, TranscriptSink, mutable_channel
from discord_bot.contracts import conforms_lifecycle_event, conforms_transcript_segment


# ── transcript.v1 (redis stream + pub/sub) ────────────────────────────────────────────────────


class FakeRedisTranscriptClient:
    def __init__(self):
        self.xadds: list[tuple[str, dict]] = []
        self.published: list[tuple[str, str]] = []

    async def xadd(self, name, fields):
        self.xadds.append((name, fields))

    async def publish(self, channel, message):
        self.published.append((channel, message))


def _segment(**overrides) -> dict:
    seg = {
        "segment_id": "sess-1:discord:42:1500",
        "speaker": "Alice",
        "speaker_key": "discord:42",
        "text": "hello there",
        "start": 1.5,
        "end": 2.5,
        "completed": True,
    }
    seg.update(overrides)
    return seg


async def test_transcript_publish_conforms_to_transcript_v1():
    segment = _segment()
    conforms_transcript_segment(segment)  # the fixture itself must be contract-faithful


async def test_transcript_publish_xadds_the_durable_stream_envelope():
    client = FakeRedisTranscriptClient()
    sink = TranscriptSink(client, meeting_id=7, native_meeting_id="222222222222222222")
    segment = _segment()
    await sink.publish(segment)

    assert len(client.xadds) == 1
    stream, fields = client.xadds[0]
    assert stream == TRANSCRIPTION_STREAM
    payload = json.loads(fields["payload"])
    assert payload["type"] == "transcription"
    assert payload["meeting_id"] == 7
    assert payload["native_meeting_id"] == "222222222222222222"
    assert payload["segments"] == [segment]


async def test_transcript_publish_also_publishes_the_live_mutable_channel():
    client = FakeRedisTranscriptClient()
    sink = TranscriptSink(client, meeting_id=7)
    segment = _segment()
    await sink.publish(segment)

    assert len(client.published) == 1
    channel, message = client.published[0]
    assert channel == mutable_channel(7) == "tc:meeting:7:mutable"
    msg = json.loads(message)
    assert msg == {"type": "transcript", "meeting": {"id": 7}, "segment": segment}


# ── lifecycle.v1 (HTTP callback, bounded retry, never raises) ────────────────────────────────


async def test_lifecycle_emit_posts_the_event_verbatim():
    seen = []

    async def fake_post(url, headers, body):
        seen.append((url, headers, json.loads(body)))
        return True, 200

    event = {"connection_id": "sess-1", "status": "active", "timestamp": "2026-01-01T00:00:00Z"}
    conforms_lifecycle_event(event)
    sink = LifecycleSink("http://meeting-api/callback", internal_secret="s3cr3t", post=fake_post)
    await sink.emit(event)

    assert len(seen) == 1
    url, headers, body = seen[0]
    assert url == "http://meeting-api/callback"
    assert headers["content-type"] == "application/json"
    assert headers["x-internal-secret"] == "s3cr3t"
    assert body == event


async def test_lifecycle_emit_retries_on_failure_then_succeeds():
    attempts = []

    async def flaky_post(url, headers, body):
        attempts.append(1)
        if len(attempts) < 3:
            return False, 503
        return True, 200

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    sink = LifecycleSink("http://x/callback", post=flaky_post, retries=5, backoff_s=0.01, sleep=fake_sleep)
    await sink.emit({"connection_id": "c", "status": "joining"})
    assert len(attempts) == 3
    assert len(sleeps) == 2  # backoff only BETWEEN attempts


async def test_lifecycle_emit_gives_up_and_never_raises():
    async def always_fails(url, headers, body):
        raise ConnectionError("unreachable")

    async def fake_sleep(s):
        return None

    logs = []
    sink = LifecycleSink(
        "http://x/callback", post=always_fails, retries=2, backoff_s=0.0,
        sleep=fake_sleep, log=logs.append,
    )
    await sink.emit({"connection_id": "c", "status": "failed"})  # must not raise
    assert any("giving up" in m for m in logs)


# ── acts.v1 (redis pub/sub ingress) ───────────────────────────────────────────────────────────


class FakeActsClient:
    def __init__(self):
        self.channel = None
        self._handler = None

    async def subscribe(self, channel, handler):
        self.channel = channel
        self._handler = handler

    async def deliver(self, message: str):
        await self._handler(message)


async def test_acts_channel_naming():
    assert acts_channel(42) == "bot_commands:meeting:42"


async def test_acts_source_dispatches_a_leave_command():
    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)
    received = []
    await source.subscribe(received.append)
    assert client.channel == "bot_commands:meeting:42"

    await client.deliver(json.dumps({"action": "leave"}))
    assert received == [{"action": "leave"}]


def _stop_py_source() -> str:
    """meeting-api's real leave-command producer, read BY PATH (not import: gate:isolation-py
    forbids discord_bot importing meeting_api). Used as a canary below so this test tracks the
    producer's actual shape instead of a hand-typed guess that could silently go stale."""
    rel = Path("meeting-api") / "src" / "meeting_api" / "lifecycle" / "stop.py"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return candidate.read_text()
    raise FileNotFoundError(f"meeting-api lifecycle/stop.py not found by path: {rel}")


async def test_acts_source_dispatches_the_real_producer_leave_shape():
    """Regression test for the acts.v1 schema/producer mismatch (see contracts.py's parse_act
    docstring): the real leave-command producer, meeting-api's lifecycle/stop.py
    leave_command_payload, sends {"action": "leave", "meeting_id": id}, not the bare
    {"action": "leave"} the golden above uses. A strict-schema parse_act silently drops this
    shape (the sealed Leave def forbids meeting_id), so every DELETE /bots/discord/{id} would be
    ignored and the bot could never be gracefully stopped."""
    src = _stop_py_source()
    assert '{"action": "leave", "meeting_id": meeting_id}' in src, (
        "stop.py's leave payload shape changed, update this golden to match"
    )

    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)
    received = []
    await source.subscribe(received.append)

    real_producer_payload = {"action": "leave", "meeting_id": 42}  # mirrors leave_command_payload(42)
    await client.deliver(json.dumps(real_producer_payload))
    assert received == [real_producer_payload]


async def test_acts_source_ignores_unknown_action():
    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)
    received = []
    await source.subscribe(received.append)

    await client.deliver(json.dumps({"action": "totally-made-up"}))
    assert received == []


async def test_acts_source_ignores_non_json_message():
    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)
    received = []
    await source.subscribe(received.append)

    await client.deliver("not json{{{")
    assert received == []


async def test_acts_source_dispatches_reconfigure():
    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)
    received = []
    await source.subscribe(received.append)

    await client.deliver(json.dumps({"action": "reconfigure", "language": "es"}))
    assert received == [{"action": "reconfigure", "language": "es"}]


async def test_acts_source_survives_a_raising_handler():
    client = FakeActsClient()
    source = ActsSource(client, meeting_id=42)

    async def bad_handler(act):
        raise RuntimeError("boom")

    await source.subscribe(bad_handler)
    await client.deliver(json.dumps({"action": "leave"}))  # must not raise out of deliver
