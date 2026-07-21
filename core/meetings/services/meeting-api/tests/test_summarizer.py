"""WP-M12 — the summary generator: candidates, prompt shape, the write, the barrier."""
import pytest

from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.summarizer import (
    MAX_PROMPT_CHARS,
    build_summary_messages,
    summarize_tick,
)

pytestmark = pytest.mark.asyncio

USER = 7


def _store(*, status="completed", data=None, segments=None) -> InMemoryTranscriptStore:
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=1, user_id=USER, platform="google_meet", native_meeting_id="nat-1",
        status=status,
        start_time="2026-07-21T09:00:00+00:00", end_time="2026-07-21T09:30:00+00:00",
        created_at="2026-07-21T08:59:00+00:00", updated_at="2026-07-21T09:30:00+00:00",
        data=data if data is not None else {
            "zaki_capture": {"state": "withdrawn", "withdrawal_reason": "capture_stopped"},
        },
        segments=segments if segments is not None else [
            {"segment_id": "s1", "start": 1.0, "end": 2.0, "speaker": "Al",
             "text": "We agreed to ship the summary generator this week.", "language": "en",
             "completed": True},
            {"segment_id": "s2", "start": 3.0, "end": 4.0, "speaker": "Nova",
             "text": "Nova owns the deploy.", "language": "en", "completed": True},
        ],
    )
    return store


async def _llm_recording(calls):
    async def llm(messages):
        calls.append(messages)
        return "## TL;DR\nShipped it."
    return llm


async def test_terminal_meeting_with_transcript_gets_a_summary():
    store = _store()
    calls: list = []
    written = await summarize_tick(store, await _llm_recording(calls), model="m-1")

    assert written == 1
    summary = store._meetings[1]["data"]["summary"]
    assert summary["text"] == "## TL;DR\nShipped it."
    assert summary["model"] == "m-1"
    assert summary["updated_at"]
    # the prompt carried the speaker-attributed lines
    user_msg = calls[0][-1]["content"]
    assert "Al: We agreed to ship the summary generator this week." in user_msg
    assert "Nova: Nova owns the deploy." in user_msg


async def test_existing_summary_is_never_regenerated():
    store = _store()
    store._meetings[1]["data"]["summary"] = {"text": "already", "updated_at": "x"}
    calls: list = []
    assert await summarize_tick(store, await _llm_recording(calls), model="m-1") == 0
    assert calls == []
    assert store._meetings[1]["data"]["summary"]["text"] == "already"


async def test_privacy_withdrawal_refuses_the_summary_write():
    store = _store(data={
        "zaki_capture": {"state": "withdrawn", "withdrawal_reason": "consent_withdrawn"},
    })
    calls: list = []
    # candidates exclude nothing in the fake beyond summary/status/text — the BARRIER refuses
    written = await summarize_tick(store, await _llm_recording(calls), model="m-1")
    assert written == 0
    assert "summary" not in store._meetings[1]["data"]


async def test_non_terminal_and_empty_transcripts_are_not_candidates():
    active = _store(status="active")
    assert await active.meetings_needing_summary(limit=5) == []
    empty = _store(segments=[{"segment_id": "s1", "start": 1.0, "end": 2.0,
                              "speaker": "Al", "text": "   ", "language": "en",
                              "completed": True}])
    assert await empty.meetings_needing_summary(limit=5) == []


async def test_llm_failure_is_contained_and_retryable():
    store = _store()

    async def bad_llm(messages):
        raise RuntimeError("backend down")

    assert await summarize_tick(store, bad_llm, model="m-1") == 0
    assert "summary" not in store._meetings[1]["data"]  # untouched → retried next tick


def test_long_transcripts_keep_head_and_tail():
    segments = [
        {"segment_id": f"s{i}", "speaker": "Al", "text": f"line {i} " + "x" * 80}
        for i in range(600)
    ]
    messages = build_summary_messages(segments)
    body = messages[-1]["content"]
    assert len(body) < MAX_PROMPT_CHARS + 500
    assert "line 0 " in body and "line 599 " in body
    assert "elided for length" in body
