"""The door as a *consumer* of the meeting API — no imports across the domain boundary.

The chat door reaches meetings the same way any customer would: over HTTP, through the
gateway, with an API key. It never imports ``meeting_api`` (that would be a bounded-context
violation, ``gate:graph-py``), and it never touches the meetings database.

Two routes, tried in order, because the record-keyed transcript route is in flight:

1. ``GET /meetings/{id}/transcript`` — the route the connector work is adding. Preferred.
2. ``GET /transcripts/by-id/{id}`` — what the gateway serves today. Fallback.

If the first answers 404/405 we fall through; any other status is surfaced as-is. When both
are unreachable the door says so on the page rather than rendering an empty record as if it
were an empty meeting — the honesty valve, same as the empty-transcript distinction the
connector leg is fixing.

The transport is injectable (``httpx.MockTransport`` in tests) — the repo's idiom for driving
the shipped code in-process against a fake upstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class MeetingRecord:
    meeting_id: str
    found: bool
    meeting: dict[str, Any] = field(default_factory=dict)
    segments: list[dict[str, Any]] = field(default_factory=list)
    transcript_available: bool = False
    note: str = ""
    """Human-readable statement of *why* something is missing. Never left implicit."""

    @property
    def title(self) -> str:
        data = self.meeting.get("data") or {}
        for key in ("name", "title", "subject"):
            value = data.get(key) or self.meeting.get(key)
            if value:
                return str(value)
        return f"meeting {self.meeting_id}"

    @property
    def platform(self) -> str:
        return str(self.meeting.get("platform") or "unknown")


class MeetingsClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=self._base, headers=headers, transport=transport, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MeetingsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, meeting_id: str) -> MeetingRecord:
        meeting, meeting_note = self._get_json(f"/meetings/{meeting_id}")
        if meeting is None:
            return MeetingRecord(
                meeting_id=meeting_id,
                found=False,
                note=meeting_note or "the meeting API did not return this record",
            )

        segments, transcript_note = self._fetch_transcript(meeting_id)
        if segments is None:
            return MeetingRecord(
                meeting_id=meeting_id,
                found=True,
                meeting=meeting,
                transcript_available=False,
                note=transcript_note or "no transcript route answered for this record",
            )
        return MeetingRecord(
            meeting_id=meeting_id,
            found=True,
            meeting=meeting,
            segments=segments,
            transcript_available=True,
            note="" if segments else "this record has a transcript resource, and it is empty",
        )

    # -- internals ----------------------------------------------------------

    def _fetch_transcript(self, meeting_id: str) -> tuple[list[dict[str, Any]] | None, str]:
        for path in (f"/meetings/{meeting_id}/transcript", f"/transcripts/by-id/{meeting_id}"):
            payload, note = self._get_json(path, soft_statuses=(404, 405))
            if payload is None:
                continue
            return _segments_of(payload), ""
        return None, note or "no transcript route answered for this record"

    def _get_json(
        self, path: str, *, soft_statuses: tuple[int, ...] = ()
    ) -> tuple[Any | None, str]:
        try:
            resp = self._client.get(path)
        except httpx.HTTPError as exc:
            return None, f"meeting API unreachable ({type(exc).__name__})"
        if resp.status_code in soft_statuses:
            return None, f"{path} answered {resp.status_code}"
        if resp.status_code >= 400:
            return None, f"{path} answered {resp.status_code}"
        try:
            return resp.json(), ""
        except ValueError:
            return None, f"{path} did not return JSON"


def _segments_of(payload: Any) -> list[dict[str, Any]]:
    """Both routes may answer a bare list or an object wrapping ``segments``."""
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    if isinstance(payload, dict):
        for key in ("segments", "transcript", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
    return []
