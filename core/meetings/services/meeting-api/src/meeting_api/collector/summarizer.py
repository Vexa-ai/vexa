"""WP-M12 — the post-meeting summary generator ("minutes-the-document").

The read plane and the archive UI have rendered ``meeting.data['summary']``
since the first activation ("Summary not available yet.") — nothing ever WROTE
it: upstream's copilot/agent stack is deliberately not deployed here. This
worker is the missing writer, engine-native and provider-thin: one sweep per
``SUMMARY_INTERVAL_S`` finds terminal ZAKI meetings that have transcript rows
and no summary, builds one prompt from the speaker-attributed segments, calls
the SAME OpenAI-compatible backend the STT leg already trusts (chat
completions instead of transcriptions; ``SUMMARY_*`` envs may point it
elsewhere), and persists ``{"text", "updated_at", "model"}`` under the shared
meeting-write barrier — so a privacy withdrawal wins against a late summary
exactly as it wins against a late transcript flush.

Deliberately NOT here: realtime copilot notes (the ``processed`` views lane),
per-user templates, regeneration. One good document per meeting first.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Protocol

from .ports import TranscriptWriteRefused

log = logging.getLogger("collector.summarizer")

#: messages → completion text. The live implementation is ``openai_chat_llm``;
#: tests inject a fake.
ChatLLM = Callable[[list[dict]], Awaitable[str]]

# Input budget: keep the prompt comfortably inside small-context serving tiers.
# Long meetings keep their head and tail (openings set agenda, endings carry
# decisions); the elided middle is marked so the model never hallucinates
# continuity across the cut.
MAX_PROMPT_CHARS = 24_000

SUMMARY_SYSTEM = (
    "You write meeting minutes. You are given a speaker-attributed transcript. "
    "Respond in the MEETING'S OWN dominant language (if the meeting mixes "
    "languages, use the one carrying the substantive discussion). Be faithful: "
    "never invent facts, names, decisions, or dates that are not in the "
    "transcript; if the transcript is too thin to support a section, write a "
    "single short line saying so.\n\n"
    "Structure the minutes exactly as:\n"
    "## TL;DR\none or two sentences.\n"
    "## Key points\nshort bullets.\n"
    "## Decisions\nbullets; only genuine decisions.\n"
    "## Action items\nbullets as 'owner — action'; only if actually assigned.\n"
    "## Open questions\nbullets; only if left unresolved."
)


class SummaryStore(Protocol):
    """The two store legs the summarizer needs (implemented by the SQL adapter
    and the in-memory fake alongside the other collector ports)."""

    async def meetings_needing_summary(self, *, limit: int) -> list[dict]:
        """Terminal ZAKI meetings owning ≥1 transcript row and no ``data.summary``
        yet — each as ``{"id", "user_id"}``, oldest first."""
        ...

    async def get_transcript_by_id(
        self, user_id: int, meeting_id: int, member_workspaces: Optional[set] = None
    ) -> Optional[dict]: ...

    async def write_summary(self, meeting_id: int, summary: dict) -> None:
        """Set ``data['summary']`` under the shared meeting-write barrier;
        raises ``TranscriptWriteRefused`` for privacy-withdrawn meetings."""
        ...


def build_summary_messages(segments: list[dict]) -> list[dict]:
    """One prompt from speaker-attributed segments, head+tail bounded."""
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = (seg.get("speaker") or "").strip() or "Speaker"
        lines.append(f"{speaker}: {text}")
    transcript = "\n".join(lines)
    if len(transcript) > MAX_PROMPT_CHARS:
        half = MAX_PROMPT_CHARS // 2
        transcript = (
            transcript[:half]
            + "\n[… middle of the meeting elided for length …]\n"
            + transcript[-half:]
        )
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Transcript:\n\n{transcript}\n\nWrite the minutes."},
    ]


def openai_chat_llm(base_url: str, token: str, model: str, *, timeout_s: float = 60.0) -> ChatLLM:
    """The live LLM leg: POST {base}/v1/chat/completions, OpenAI-compatible."""
    import httpx

    url = base_url.rstrip("/")
    if not url.endswith("/v1/chat/completions"):
        url = f"{url}/v1/chat/completions"

    async def call(messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"model": model, "messages": messages, "temperature": 0.2},
            )
            response.raise_for_status()
            body = response.json()
            text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("summary backend returned an empty completion")
            return text.strip()

    return call


async def summarize_tick(
    store: SummaryStore,
    llm: ChatLLM,
    *,
    model: str,
    limit: int = 3,
    now: Optional[datetime] = None,
) -> int:
    """ONE sweep: summarize up to ``limit`` candidates. Per-meeting failures are
    contained (the next tick retries); a privacy-refused write is final for that
    meeting and logged loudly (the candidates query stops offering it once the
    erasure purges the row). Returns the number of summaries written."""
    written = 0
    for candidate in await store.meetings_needing_summary(limit=limit):
        meeting_id = int(candidate["id"])
        try:
            doc = await store.get_transcript_by_id(int(candidate["user_id"]), meeting_id)
            segments = (doc or {}).get("segments") or []
            if not any((seg.get("text") or "").strip() for seg in segments):
                continue  # rows may exist with empty text only — nothing to summarize
            text = await llm(build_summary_messages(segments))
            stamp = (now or datetime.now(timezone.utc)).isoformat()
            await store.write_summary(
                meeting_id, {"text": text, "updated_at": stamp, "model": model}
            )
            written += 1
            log.info("summary written for meeting %s (%d segments)", meeting_id, len(segments))
        except TranscriptWriteRefused:
            log.warning(
                "summary write refused for meeting %s (privacy barrier) — not retrying", meeting_id
            )
        except Exception:  # noqa: BLE001 — isolate per meeting; the next tick retries
            log.exception("summary generation failed for meeting %s", meeting_id)
    return written
