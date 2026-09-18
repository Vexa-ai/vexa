"""Offline streaming adapter and real HTTP contract, using SDK result objects."""
import asyncio
import importlib
import io
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
import pytest
import soundfile as sf
from amazon_transcribe.exceptions import (
    BadRequestException, InternalFailureException, LimitExceededException,
    ServiceUnavailableException,
)
from amazon_transcribe.model import Alternative, Item, Result, Transcript, TranscriptEvent
from amazon_transcribe.auth import Credentials
from amazon_transcribe.eventstream import EventSigner
from amazon_transcribe.model import AudioStream
from amazon_transcribe.serialize import AudioEventSerializer
from fastapi.testclient import TestClient

from transcription import aws_transcribe as backend
from transcription import main as svc


SEGMENT_KEYS = {
    "id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob",
    "compression_ratio", "no_speech_prob", "audio_start", "audio_end",
}


@pytest.fixture(autouse=True)
def isolated_credential_provider(monkeypatch):
    monkeypatch.setattr(backend, "_credential_provider", None)


class CountingProvider:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def get_credentials(self):
        self.calls += 1
        future = Future()
        if self.error:
            future.set_exception(self.error)
        else:
            future.set_result(Credentials("test-access", "test-secret"))
        return future


def event(text, start, end, partial=False):
    return TranscriptEvent(Transcript([Result(
        result_id=str(start), start_time=start, end_time=end, is_partial=partial,
        alternatives=[Alternative(text, [
            Item(start, end, "pronunciation", text.rstrip(".")),
            Item(item_type="punctuation", content="."),
        ], [])],
    )]))


class FakeStream:
    def __init__(self):
        self.input_stream = self
        self.chunks = []
        self.ended = False
        self.receiving = asyncio.Event()
        self.sent = asyncio.Event()
        self.receive_finished = False

    async def send_audio_event(self, *, audio_chunk):
        # Sending must run concurrently with consuming the output stream.
        await self.receiving.wait()
        self.chunks.append(audio_chunk)
        self.sent.set()

    async def end_stream(self):
        self.ended = True

    @property
    def output_stream(self):
        return self.events()

    async def events(self):
        self.receiving.set()
        try:
            await self.sent.wait()
            yield event("Hel", 0, 0.5, partial=True)
            yield event("Hello.", 0, 1)
            yield event("World.", 1.25, 2.5)
        finally:
            self.receive_finished = True


class FakeClient:
    def __init__(self, stream=None, error=None):
        self.stream = stream or FakeStream()
        self.error = error
        self.kwargs = None

    async def start_stream_transcription(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.stream


def wav(sample_rate=16000):
    buf = io.BytesIO()
    samples = np.array([-32768, -16384, 0, 16384, 32767] * 1000, dtype=np.int16)
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue(), samples.astype("<i2").tobytes()


@pytest.fixture
def transcribe(monkeypatch):
    monkeypatch.setattr(svc, "STT_BACKEND", "transcribe")
    monkeypatch.setattr(svc, "API_TOKEN", "")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("TRANSCRIBE_LANGUAGE_DEFAULT", raising=False)
    monkeypatch.delenv("TRANSCRIBE_TIMEOUT_S", raising=False)
    fake = FakeClient()

    def factory(*, region):
        assert region == "eu-west-1"
        return fake

    monkeypatch.setattr(backend, "create_client", factory)

    def no_whisper(*args, **kwargs):
        pytest.fail("Transcribe must not invoke Whisper or its heuristics")

    monkeypatch.setattr(svc, "WhisperModel", no_whisper)
    monkeypatch.setattr(svc, "_looks_like_silence", no_whisper)
    monkeypatch.setattr(svc, "_looks_like_hallucination", no_whisper)
    return fake


def post(client, **fields):
    audio, _ = wav()
    return client.post("/v1/audio/transcriptions",
                       files={"file": ("a.wav", audio, "audio/wav")},
                       data={"model": "whisper-1", **fields})


def test_verbose_contract_and_pcm(client, transcribe):
    response = post(client, language="en")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"text", "language", "language_probability", "duration", "segments"}
    assert body["text"] == "Hello. World."
    assert body["language"] == "en-US"
    assert body["language_probability"] == 1.0
    assert body["duration"] == 2.5
    assert len(body["segments"]) == 2
    for idx, (segment, times, text) in enumerate(zip(
        body["segments"], [(0, 1), (1.25, 2.5)], ["Hello.", "World."]
    )):
        assert set(segment) == SEGMENT_KEYS
        assert segment == {
            "id": idx, "seek": 0, "start": times[0], "end": times[1], "text": text,
            "tokens": [], "temperature": 0.0, "avg_logprob": 0.0,
            "compression_ratio": 1.0, "no_speech_prob": 0.0,
            "audio_start": times[0], "audio_end": times[1],
        }
    assert transcribe.kwargs == {
        "language_code": "en-US", "media_sample_rate_hz": 16000, "media_encoding": "pcm",
    }
    assert b"".join(transcribe.stream.chunks) == wav()[1]
    assert max(map(len, transcribe.stream.chunks)) <= 32768
    assert transcribe.stream.ended


def test_words_only_when_requested(client, transcribe):
    response = post(client, timestamp_granularities="word", language="de")
    assert response.status_code == 200
    assert response.json()["language"] == "de-DE"
    segment = response.json()["segments"][0]
    assert set(segment) == SEGMENT_KEYS | {"words"}
    assert segment["words"] == [{"word": "Hello", "start": 0, "end": 1, "probability": 1.0}]


def test_file_sample_rate_is_preserved(client, transcribe):
    audio, _ = wav(8000)
    response = client.post("/v1/audio/transcriptions",
                           files={"file": ("a.wav", audio, "audio/wav")},
                           data={"model": "whisper-1"})
    assert response.status_code == 200
    assert transcribe.kwargs["media_sample_rate_hz"] == 8000


@pytest.mark.parametrize("error,status,retry", [
    (LimitExceededException("limit"), 503, "1"),
    (ServiceUnavailableException("unavailable"), 503, "1"),
    (InternalFailureException("internal"), 503, "1"),
    (asyncio.TimeoutError(), 503, "1"),
    (BadRequestException("bad request"), 422, None),
    (backend.CredentialsUnavailable("AWS credentials could not be resolved"), 503, "5"),
])
def test_error_mapping(client, transcribe, error, status, retry):
    transcribe.error = error
    response = post(client)
    assert response.status_code == status
    assert response.headers.get("Retry-After") == retry
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]
    assert svc.waiting_requests == svc.active_realtime_requests == 0
    assert svc.transcription_semaphore._value == svc.MAX_CONCURRENT_TRANSCRIPTIONS


def test_garbage_is_422_without_stream(client, transcribe):
    response = client.post("/v1/audio/transcriptions",
                           files={"file": ("bad.wav", b"garbage", "audio/wav")},
                           data={"model": "whisper-1"})
    assert response.status_code == 422
    assert transcribe.kwargs is None


@pytest.mark.parametrize("fails", [False, True])
def test_health_with_real_resolver_wrapper(client, transcribe, monkeypatch, fails):
    provider = CountingProvider(RuntimeError("unresolvable credentials") if fails else None)
    monkeypatch.setattr(backend.AwsCredentialsProvider, "new_default_chain", lambda _: provider)
    response = client.get("/health")
    assert response.status_code == (503 if fails else 200)
    assert response.json()["backend"] == "transcribe"
    assert response.json()["region"] == "eu-west-1"
    assert response.json()["status"] == ("unhealthy" if fails else "healthy")
    if fails:
        assert response.json()["reason"] == "credentials unresolvable: RuntimeError: unresolvable credentials"


def test_health_credential_reason_is_truncated(client, transcribe, monkeypatch):
    message = "provider unavailable " + "x" * 300
    provider = CountingProvider(RuntimeError(message))
    monkeypatch.setattr(backend.AwsCredentialsProvider, "new_default_chain", lambda _: provider)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["reason"] == f"credentials unresolvable: RuntimeError: {message}"[:200]
    assert len(response.json()["reason"]) == 200


def test_missing_region_health(client, transcribe, monkeypatch):
    monkeypatch.delenv("AWS_REGION")
    response = client.get("/health")
    assert response.status_code == 503
    assert "AWS_REGION" in response.json()["reason"]


@pytest.mark.parametrize("value", ["0", "-1", "30", "90", "nan", "inf", "bad"])
def test_invalid_timeout_health(client, transcribe, monkeypatch, value):
    monkeypatch.setenv("TRANSCRIBE_TIMEOUT_S", value)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["reason"]


@pytest.mark.parametrize("source,expected", [
    ("en", "en-US"), ("de", "de-DE"), ("fr", "fr-FR"), ("es", "es-ES"),
    ("it", "it-IT"), ("pt", "pt-PT"), ("nl", "nl-NL"), ("ja", "ja-JP"),
    ("ko", "ko-KR"), ("zh", "zh-CN"), ("en-GB", "en-GB"),
    (None, "fr-CA"), ("unknown", "fr-CA"),
])
def test_language_mapping(source, expected):
    assert backend.map_language(source, "fr-CA") == expected


def test_language_default_override(client, transcribe, monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_LANGUAGE_DEFAULT", "fr-CA")
    assert post(client).json()["language"] == "fr-CA"


def test_default_backend_keys_and_auth(monkeypatch):
    with monkeypatch.context() as patch:
        patch.delenv("STT_BACKEND", raising=False)
        patch.setenv("API_TOKEN", "test-secret")
        importlib.reload(svc)
        try:
            client = TestClient(svc.app)
            response = client.get("/health")
            expected = {"status", "worker_id", "timestamp", "model", "device", "gpu_available", "backend"}
            if svc.DEVICE == "cuda":
                expected.add("compute_type")
            assert set(response.json()) == expected
            assert response.json()["backend"] == "whisper"
            assert response.status_code == 503
            assert post(client).status_code == 401
        finally:
            # Restore the env before reloading so subsequent tests retain their config.
            patch.undo()
            importlib.reload(svc)


def test_transcribe_auth_is_shared(client, transcribe, monkeypatch):
    monkeypatch.setattr(svc, "API_TOKEN", "test-secret")
    assert post(client).status_code == 401
    assert transcribe.kwargs is None


@pytest.mark.asyncio
async def test_startup_does_not_load_model(transcribe):
    await svc.startup_event()
    assert svc.model is None


@pytest.mark.asyncio
async def test_timeout_cancels_both_stream_tasks():
    stream = FakeStream()
    fake = FakeClient(stream)
    # No audio: receive waits indefinitely, until the deadline cancels it.
    with pytest.raises(asyncio.TimeoutError):
        await backend.transcribe_pcm16(b"", 16000, "en-US", region="eu-west-1",
                                       timeout_s=0.01, client_factory=lambda **_: fake)
    assert stream.ended
    assert stream.receive_finished


@pytest.mark.asyncio
async def test_output_failure_stops_audio_sender():
    class FailedOutput(FakeStream):
        async def events(self):
            raise LimitExceededException("limit")
            yield  # make this an async iterator

    stream = FailedOutput()
    with pytest.raises(LimitExceededException):
        await backend.transcribe_pcm16(b"\0\0", 16000, "en-US", region="eu-west-1",
                                       timeout_s=1, client_factory=lambda **_: FakeClient(stream))
    assert stream.ended


@pytest.mark.asyncio
async def test_empty_results_have_zero_duration():
    class EmptyOutput(FakeStream):
        async def events(self):
            self.receiving.set()
            yield TranscriptEvent(Transcript([]))

    segments, duration = await backend.transcribe_pcm16(
        b"\0\0", 16000, "en-US", region="eu-west-1", timeout_s=1,
        client_factory=lambda **_: FakeClient(EmptyOutput()),
    )
    assert segments == []
    assert duration == 0.0


def test_fifty_chunks_share_signing_credentials_and_health_provider(client, monkeypatch):
    monkeypatch.setattr(svc, "STT_BACKEND", "transcribe")
    monkeypatch.setattr(svc, "API_TOKEN", "")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("TRANSCRIBE_TIMEOUT_S", raising=False)
    provider = CountingProvider()
    constructions = []
    streams = []

    def new_chain(bootstrap):
        constructions.append(bootstrap)
        return provider

    monkeypatch.setattr(backend.AwsCredentialsProvider, "new_default_chain", new_chain)

    class SignedStream(FakeStream):
        def __init__(self, resolver):
            super().__init__()
            self.signed_input = AudioStream(
                event_serializer=AudioEventSerializer(),
                event_signer=EventSigner("transcribe", "eu-west-1"),
                initial_signature=b"\0" * 32, credential_resolver=resolver,
            )

        async def send_audio_event(self, *, audio_chunk):
            await self.signed_input.send_audio_event(audio_chunk=audio_chunk)
            await super().send_audio_event(audio_chunk=audio_chunk)

        async def end_stream(self):
            await self.signed_input.end_stream()
            await super().end_stream()

    class SigningClient:
        def __init__(self, *, region, credential_resolver):
            assert region == "eu-west-1"
            self.resolver = credential_resolver

        async def start_stream_transcription(self, **kwargs):
            # The real SDK resolves once for the HTTP signature, then the real
            # AudioStream below resolves again for every event and the end marker.
            await self.resolver.get_credentials()
            stream = SignedStream(self.resolver)
            streams.append(stream)
            return stream

    monkeypatch.setattr(backend, "TranscribeStreamingClient", SigningClient)
    assert constructions == []  # lazy, no credential I/O at import/startup
    assert client.get("/health").status_code == 200
    buf = io.BytesIO()
    sf.write(buf, np.zeros(16000 * 5, dtype=np.int16), 16000, format="WAV", subtype="PCM_16")
    response = client.post("/v1/audio/transcriptions",
                           files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                           data={"model": "whisper-1"})
    assert response.status_code == 200
    assert len(streams[0].chunks) == 50
    assert streams[0].ended
    assert provider.calls == 2  # one health probe and one initial signing resolution
    assert len(constructions) == 1

    # A new request refreshes through the same provider, not a process-wide frozen key.
    assert post(client).status_code == 200
    assert len(constructions) == 1
    assert provider.calls == 3


def test_provider_initialization_is_thread_safe(monkeypatch):
    import threading
    import time

    provider = CountingProvider()
    constructions = []
    barrier = threading.Barrier(8)

    def new_chain(_):
        constructions.append(True)
        time.sleep(0.01)  # release the GIL while other callers reach initialization
        return provider

    monkeypatch.setattr(backend.AwsCredentialsProvider, "new_default_chain", new_chain)

    def get_provider(_):
        barrier.wait(timeout=2)
        return backend._get_credential_provider()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(item is provider for item in pool.map(get_provider, range(8)))
    assert len(constructions) == 1


def test_foreign_events_between_finals_are_ignored(client, transcribe):
    class ForeignEvents(FakeStream):
        async def events(self):
            self.receiving.set()
            yield event("Hello.", 0, 1)
            yield None
            yield object()
            yield event("World.", 1.25, 2.5)

    transcribe.stream = ForeignEvents()
    response = post(client)
    assert response.status_code == 200
    assert response.json()["text"] == "Hello. World."
    assert [s["id"] for s in response.json()["segments"]] == [0, 1]


@pytest.mark.parametrize("alternatives", [[], None])
def test_missing_alternatives_are_ignored(client, transcribe, alternatives):
    class NoAlternatives(FakeStream):
        async def events(self):
            self.receiving.set()
            yield TranscriptEvent(Transcript([Result(
                start_time=0, end_time=1, is_partial=False, alternatives=alternatives,
            )]))

    transcribe.stream = NoAlternatives()
    response = post(client)
    assert response.status_code == 200
    assert response.json()["segments"] == []
    assert response.json()["text"] == ""
    assert response.json()["duration"] == 0.0


def test_duration_uses_maximum_end_without_rewriting_segment_times(client, transcribe):
    class ReversedTimes(FakeStream):
        async def events(self):
            self.receiving.set()
            yield event("First.", 0, 5)
            yield event("Second.", 4, 2)

    transcribe.stream = ReversedTimes()
    response = post(client)
    assert response.status_code == 200
    assert response.json()["duration"] == 5
    assert [(s["start"], s["end"]) for s in response.json()["segments"]] == [(0, 5), (4, 2)]
