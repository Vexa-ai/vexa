"""Fakes and record builders.

Two rules the fixtures here follow, both learned from what the corpus run found:

* **The gate is never faked.** Every test that reaches the gate stage runs the real
  ``presend_gate`` module. A fake gate would let a pipeline change that quietly broadens the
  recipient list pass, which is the one failure this whole service exists to prevent.
* **The gateway is faked at the transport, not at the client.** Tests inject an
  ``httpx.MockTransport`` (or the shipped :class:`CorpusTransport`) so the real
  :class:`HttpMeetingGateway` — its routes, its fall-through, its note text — is the code
  under test.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import httpx

from vexa_artifact_pipeline import Artifact, DeliveryResult, Recipient

DIALOGUE = [
    ("Dmitry Grankin", "So what did you decide on the pricing question? I want to know before Friday."),
    ("Marvin Hanke", "We decided to keep the per-seat number and revisit it after the pilot ends."),
    ("Dmitry Grankin", "Good. I will send you the updated proposal with those numbers tomorrow morning."),
    ("Marvin Hanke", "I'll show it to Toby today and dogfood it on our internal German meetings."),
    ("Dmitry Grankin", "How long do you need for the integration work on your side, Marvin?"),
    ("Marvin Hanke", "Two weeks if nothing else lands on us, three if the migration slips again."),
    ("Dmitry Grankin", "Understood. I'll also write up the smoke test so a broken transcript says where."),
    ("Marvin Hanke", "Can you send Laiba the OIDC details as well before the end of this week?"),
    ("Laiba Warraich", "I will collect the Entra ID configuration and forward it to both of you."),
    ("Dmitry Grankin", "That works. We agreed to meet again in two weeks with the results in hand."),
]


def segment(speaker: str, text: str, start: float, language: str = "en") -> dict[str, Any]:
    return {
        "speaker": speaker,
        "text": text,
        "start": start,
        "end": start + 6.0,
        "language": language,
    }


def conversation(
    lines: Sequence[tuple[str, str]] = tuple(DIALOGUE), repeats: int = 3, language: str = "en"
) -> list[dict[str, Any]]:
    """A record's worth of turn-taking speech — long enough to clear the gate's floor."""
    out: list[dict[str, Any]] = []
    t = 0.0
    for _ in range(repeats):
        for speaker, text in lines:
            out.append(segment(speaker, text, t, language))
            t += 6.0
    return out


def monologue(speaker: str, repeats: int = 40, language: str = "en") -> list[dict[str, Any]]:
    """One voice, no turn-taking — the shape of played-back audio, not a call."""
    line = (
        "the court then considered the second appeal and rejected it on procedural grounds "
        "which the parties had already anticipated in their filings"
    )
    return [segment(speaker, line, i * 6.0, language) for i in range(repeats)]


def record_payload(
    meeting_id: int | str,
    *,
    segments: list[dict[str, Any]] | None = None,
    participants: Iterable[str] = (),
    platform: str = "teams",
    start: str = "2026-05-18T09:00:00Z",
    end: str = "2026-05-18T10:00:00Z",
    name: str = "Pilot sync",
) -> dict[str, Any]:
    return {
        "id": meeting_id,
        "platform": platform,
        "start_time": start,
        "end_time": end,
        "segments": segments if segments is not None else conversation(),
        "data": {"name": name, "participants": list(participants)},
    }


def transport_for(records: dict[str, dict[str, Any]]) -> httpx.MockTransport:
    """Serve ``/meetings/{id}`` and ``/meetings/{id}/transcript`` from a dict of payloads.

    Keyed by the id a caller *asks for*, which is not necessarily the id the payload states
    — that divergence is real (six of twenty-two corpus records) and is what the record-id
    assertions exercise. A lookup falls back to the payload's own ``id``, the same resolution
    the shipped :class:`CorpusTransport` performs, so both spellings of one record resolve.
    """

    def resolve(meeting_id: str) -> dict[str, Any] | None:
        if meeting_id in records:
            return records[meeting_id]
        return next((p for p in records.values() if str(p.get("id")) == meeting_id), None)

    def handler(request: httpx.Request) -> httpx.Response:
        parts = [p for p in request.url.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "meetings":
            payload = resolve(parts[1])
            if payload is None:
                return httpx.Response(404, json={"detail": "unknown record"})
            if len(parts) == 2:
                return httpx.Response(200, json={k: v for k, v in payload.items() if k != "segments"})
            if parts[2] == "transcript":
                return httpx.Response(200, json={"segments": payload.get("segments") or []})
        if len(parts) == 3 and parts[:2] == ["transcripts", "by-id"]:
            payload = resolve(parts[2])
            if payload is None:
                return httpx.Response(404, json={"detail": "unknown record"})
            return httpx.Response(200, json={"segments": payload.get("segments") or []})
        return httpx.Response(404, json={"detail": f"no route {request.url.path}"})

    return httpx.MockTransport(handler)


class RecordingDelivery:
    """A delivery sink that remembers everything and sends nothing."""

    name = "recording"

    def __init__(self, *, requires_address: bool = True, fail: set[str] | None = None) -> None:
        self.requires_address = requires_address
        self.sent: list[tuple[str, Artifact]] = []
        self._fail = fail or set()

    def deliver(self, artifact: Artifact, recipient: Recipient) -> DeliveryResult:
        if recipient.identity in self._fail:
            return DeliveryResult(status=DeliveryResult.FAILED, detail="fake failure")
        self.sent.append((recipient.identity, artifact))
        return DeliveryResult(
            status=DeliveryResult.SENT, detail="recording", reference=f"fake:{len(self.sent)}"
        )

    @property
    def identities(self) -> list[str]:
        return [identity for identity, _ in self.sent]
