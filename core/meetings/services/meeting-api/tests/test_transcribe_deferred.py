"""RED fixtures for #525 C1: deferred transcription from the recording (D-A2 lane).

Hand-authored edge fixtures FROM the #355 report, as #525 mandates. Each pins one defect
of the 0.10 implementation as a regression floor for the new ``meeting_api.transcribe``
module:

  * defect 1: a Groq-shaped ``model_not_found`` 404 body surfaces as a TYPED fault that
    carries the provider's code and message, never a generic 502 or a silent empty;
  * defect 2: a default-``json`` response WITHOUT ``segments[]`` (response_format not
    honored) is loud, never "0 segments, speakers=[]" with a green status;
  * defect 3: ``language: "English"`` is normalized to ISO-639-1 ``en`` at STORAGE time
    (A3; the 0.10 read-side validator that crashed on it no longer exists).

RED by construction: ``meeting_api.transcribe`` does not exist yet, so this file is the
module's contract. Green = C1 implemented as prepared in #525 and ruled on 2026-07-21:
serve fork; master audio via the recordings seam; STT call with the configured model +
``response_format=verbose_json`` + ``transcription_tier=deferred`` (503 + Retry-After
looped); rows through the collector's durable-write seam; second run refused as a
conflict (Q2); meeting-api owns the route (Q4).
"""
from __future__ import annotations

import json

import httpx
import pytest

from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.transcribe import HttpSttTranscriber, TranscribeFault, transcribe_meeting

# --- fixtures, verbatim from the #355 report --------------------------------------------

# Defect 1: Groq's exact 404 body for the wrong model id ``large-v3-turbo`` (the correct
# id is ``whisper-large-v3-turbo``). 0.10 collapsed this into HTTPException 502
# "Transcription service error: 404" and the user saw a generic gateway error.
GROQ_MODEL_NOT_FOUND = {
    "error": {
        "message": "The model `large-v3-turbo` does not exist or you do not have access to it.",
        "type": "invalid_request_error",
        "code": "model_not_found",
    }
}

# Defect 2: with response_format omitted, Groq's /audio/transcriptions defaults to the
# plain ``json`` format: a top-level text and NO segments key. 0.10 read
# ``tx_result.get("segments", [])`` and silently stored nothing.
DEFAULT_JSON_NO_SEGMENTS = {"text": "Deferred transcription of a real meeting."}

# Defect 3: Groq names the language in full. 0.10 stored it verbatim; every deferred
# segment then failed the read-side ISO validator and was dropped from the transcript.
VERBOSE_JSON_LANGUAGE_ENGLISH = {
    "text": " Hello everyone. Let's get started.",
    "language": "English",
    "duration": 8.2,
    "segments": [
        {
            "id": 0, "seek": 0, "start": 0.0, "end": 3.4, "text": " Hello everyone.",
            "tokens": [], "temperature": 0.0, "avg_logprob": -0.21,
            "compression_ratio": 1.08, "no_speech_prob": 0.01,
        },
        {
            "id": 1, "seek": 340, "start": 3.4, "end": 8.2, "text": " Let's get started.",
            "tokens": [], "temperature": 0.0, "avg_logprob": -0.18,
            "compression_ratio": 1.11, "no_speech_prob": 0.02,
        },
    ],
}

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "  # raw master bytes; content is opaque to the module


# --- harness ----------------------------------------------------------------------------

def make_stt(handler, model="whisper-large-v3-turbo"):
    """The prod STT adapter over httpx.MockTransport (the house pattern for HTTP seams)."""
    return HttpSttTranscriber(
        base_url="http://stt.test",
        token="test-stt-token",
        model=model,
        transport=httpx.MockTransport(handler),
    )


def seeded_store(**kw):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(
        user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="completed", **kw,
    )
    return store, mid


async def resolve_master(meeting_id):
    """The recordings-seam stub: a finalized master exists for the meeting."""
    return WAV


async def run(store, mid, stt, *, user_id=7, resolver=resolve_master, language=None):
    return await transcribe_meeting(
        store=store, stt=stt, resolve_master=resolver,
        user_id=user_id, meeting_id=mid, language=language,
    )


# --- defect 1: provider rejection is a typed fault, carried loudly ----------------------

async def test_groq_model_not_found_is_a_typed_fault_not_a_generic_502():
    def handler(request):
        return httpx.Response(404, json=GROQ_MODEL_NOT_FOUND)

    store, mid = seeded_store()
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(handler, model="large-v3-turbo"))

    fault = ei.value
    assert fault.kind == "provider_rejected"
    assert fault.status == 404
    assert fault.provider_code == "model_not_found"
    # The provider's own message travels with the fault: an operator must see WHY, not
    # 0.10's "Transcription service error: 404".
    assert "large-v3-turbo" in str(fault)

    doc = await store.get_transcript_by_id(7, mid)
    assert doc["segments"] == []  # nothing stored on a rejected run


# --- defect 2: a segments-less body is loud, never a silent zero-row success ------------

async def test_missing_segments_key_is_loud_not_a_silent_empty():
    # NOTE: a genuine verbose_json ``"segments": []`` (silent audio) is NOT this fault;
    # the defect is the ABSENT key, meaning response_format was not honored.
    def handler(request):
        return httpx.Response(200, json=DEFAULT_JSON_NO_SEGMENTS)

    store, mid = seeded_store()
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(handler))

    fault = ei.value
    assert fault.kind == "no_segments"
    assert "verbose_json" in str(fault)  # the fault names the response_format contract

    doc = await store.get_transcript_by_id(7, mid)
    assert doc["segments"] == []


# --- defect 3: language normalized to ISO-639-1 at storage time (A3) --------------------

async def test_language_english_is_normalized_to_iso_at_storage():
    def handler(request):
        return httpx.Response(200, json=VERBOSE_JSON_LANGUAGE_ENGLISH)

    store, mid = seeded_store()
    result = await run(store, mid, make_stt(handler))

    assert result["meeting_id"] == mid
    assert result["segments_stored"] == 2
    assert result["language"] == "en"

    doc = await store.get_transcript_by_id(7, mid)
    segs = sorted(doc["segments"], key=lambda s: s["start"])
    assert len(segs) == 2
    assert [s["language"] for s in segs] == ["en", "en"]  # never "English" in a row
    assert segs[0]["text"].strip() == "Hello everyone."
    assert segs[1]["text"].strip() == "Let's get started."
    assert (segs[0]["start"], segs[0]["end"]) == (0.0, 3.4)
    assert (segs[1]["start"], segs[1]["end"]) == (3.4, 8.2)
    # Distinct, truthy segment ids: the collector upsert drops id-less rows silently.
    assert len({s["segment_id"] for s in segs}) == 2


# --- the outbound request: the #355 root causes, pinned at the wire ---------------------

async def test_request_carries_model_verbose_json_and_deferred_tier():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=VERBOSE_JSON_LANGUAGE_ENGLISH)

    store, mid = seeded_store()
    await run(store, mid, make_stt(handler), language="en")

    (req,) = seen
    # Bare base URL gets the OpenAI-compatible path appended (append-only-when-missing,
    # the one rule the TS client and config_preflight already share).
    assert req.url.path == "/v1/audio/transcriptions"
    assert req.headers.get("authorization") == "Bearer test-stt-token"

    body = req.read()
    assert b'name="file"' in body
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in body      # defect-1 floor
    assert b'name="response_format"\r\n\r\nverbose_json' in body      # defect-2 floor
    assert b'name="transcription_tier"\r\n\r\ndeferred' in body       # the deferred tier
    assert b'name="language"\r\n\r\nen' in body                       # caller hint forwarded


# --- deferred tier backpressure: 503 + Retry-After is looped, not fatal -----------------

async def test_busy_503_is_retried_per_retry_after():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                503, headers={"Retry-After": "0"},
                json={"detail": "Service busy handling maximum concurrent requests"},
            )
        return httpx.Response(200, json=VERBOSE_JSON_LANGUAGE_ENGLISH)

    store, mid = seeded_store()
    result = await run(store, mid, make_stt(handler))

    assert len(calls) == 2
    assert result["segments_stored"] == 2


# --- prepared edges: absent master, ownership, and the Q2 conflict ruling ---------------

async def test_absent_master_is_a_no_recording_fault_not_a_500():
    async def no_master(meeting_id):
        return None  # finalize_master's None: recording disabled or nothing to finalize

    store, mid = seeded_store()
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(lambda r: httpx.Response(500)), resolver=no_master)
    assert ei.value.kind == "no_recording"


async def test_foreign_meeting_is_not_found():
    store, mid = seeded_store()  # owned by user 7
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(lambda r: httpx.Response(500)), user_id=8)
    assert ei.value.kind == "not_found"


async def test_active_meeting_is_refused_not_partially_transcribed():
    # finalize-on-read assembles a PARTIAL master mid-recording (#768); transcribing it would
    # store a partial transcript that then 409-blocks the full run forever. Refuse, typed.
    store, mid = seeded_store()
    store._meetings[mid]["status"] = "active"
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(lambda r: httpx.Response(500)))
    assert ei.value.kind == "not_completed"

    doc = await store.get_transcript_by_id(7, mid)
    assert doc["segments"] == []


async def test_transcript_share_viewer_cannot_transcribe():
    # Transcription is an OWNER-tier mutation (provider cost, rows written, the single Q2 run
    # consumed); the read-tier ACL admits share viewers, the transcribe gate must not.
    store, mid = seeded_store(data={"transcript_viewers": [9]})
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(lambda r: httpx.Response(500)), user_id=9)
    assert ei.value.kind == "not_found"


async def test_concurrent_second_run_is_refused_while_first_transcribes():
    import asyncio

    release = asyncio.Event()

    class SlowStt:
        async def transcribe(self, audio, *, language=None):
            await release.wait()
            return VERBOSE_JSON_LANGUAGE_ENGLISH

    store, mid = seeded_store()
    first = asyncio.create_task(run(store, mid, SlowStt()))
    await asyncio.sleep(0)  # let the first run enter the STT await

    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, SlowStt())
    assert ei.value.kind == "already_running"

    release.set()
    result = await first
    assert result["segments_stored"] == 2


async def test_second_transcription_is_refused_as_conflict():
    # Q2 ruling (2026-07-21): keep 0.10's refuse-second-run semantics, typed.
    store, mid = seeded_store(segments=[
        {"segment_id": "deferred:0:0.000", "start": 0.0, "end": 3.4,
         "text": "Hello everyone.", "language": "en"},
    ])
    with pytest.raises(TranscribeFault) as ei:
        await run(store, mid, make_stt(lambda r: httpx.Response(500)))
    assert ei.value.kind == "already_transcribed"


# --- #522 semantics: the model id comes from the deployment, default whisper-1 ----------

async def test_from_env_model_resolution(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
    stt = HttpSttTranscriber.from_env()
    assert stt.model == "whisper-large-v3-turbo"
    assert stt.base_url == "http://stt.test/transcribe"  # conftest's autouse STT env
    assert stt.token == "test-stt-token"

    monkeypatch.delenv("TRANSCRIPTION_MODEL")
    assert HttpSttTranscriber.from_env().model == "whisper-1"
