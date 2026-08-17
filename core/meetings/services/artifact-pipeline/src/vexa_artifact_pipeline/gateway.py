"""Gather — the meeting record, fetched the way a customer would fetch it.

The pipeline is a *consumer* of the meeting API: HTTP, through the gateway, with an API
key. It never imports ``meeting_api`` (that is a bounded-context violation, ``gate:graph-py``)
and never reads the meetings database.

Two transcript routes are tried in order, because the record-keyed one is in flight:

1. ``GET /meetings/{id}/transcript`` — record-keyed. Preferred.
2. ``GET /transcripts/by-id/{id}`` — what the gateway serves today. Fallback.

A 404/405 on the first falls through; any other status is surfaced as-is in
:attr:`FetchedRecord.note`. When neither answers, the record is returned *not*
transcript-available rather than as an empty meeting — the same honesty valve the
connector work put on the API itself. An empty list and an unreachable route are different
facts and the pipeline treats them differently.

The transport is injectable, which is how the corpus test drives this shipped client
against fixture payloads with no server in the loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .ports import FetchedRecord

TRANSCRIPT_ROUTES = ("/meetings/{id}/transcript", "/transcripts/by-id/{id}")


class HttpMeetingGateway:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=self._base, headers=headers, transport=transport, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpMeetingGateway":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, meeting_id: str) -> FetchedRecord:
        payload, note = self._get_json(f"/meetings/{meeting_id}")
        if payload is None:
            return FetchedRecord(
                requested_id=str(meeting_id),
                found=False,
                note=note or "the meeting API did not return this record",
            )
        if not isinstance(payload, dict):
            return FetchedRecord(
                requested_id=str(meeting_id),
                found=False,
                note=f"/meetings/{meeting_id} returned {type(payload).__name__}, not a record object",
            )

        inline = payload.get("segments")
        if isinstance(inline, list) and inline:
            return FetchedRecord(
                requested_id=str(meeting_id),
                found=True,
                payload=payload,
                segments=[s for s in inline if isinstance(s, dict)],
                transcript_available=True,
            )

        segments, transcript_note = self._fetch_transcript(meeting_id)
        if segments is None:
            return FetchedRecord(
                requested_id=str(meeting_id),
                found=True,
                payload=payload,
                transcript_available=False,
                note=transcript_note or "no transcript route answered for this record",
            )
        return FetchedRecord(
            requested_id=str(meeting_id),
            found=True,
            payload=payload,
            segments=segments,
            transcript_available=True,
            note="" if segments else "this record has a transcript resource, and it is empty",
        )

    # -- internals ----------------------------------------------------------

    def _fetch_transcript(self, meeting_id: str) -> tuple[list[dict[str, Any]] | None, str]:
        note = ""
        for template in TRANSCRIPT_ROUTES:
            payload, note = self._get_json(
                template.format(id=meeting_id), soft_statuses=(404, 405)
            )
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
        if resp.status_code in soft_statuses or resp.status_code >= 400:
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


class CorpusTransport(httpx.BaseTransport):
    """A dev/test transport that answers the two routes from harvested record JSON on disk.

    This is not a second client: the shipped :class:`HttpMeetingGateway` runs unchanged on
    top of it, so a corpus run exercises the same request paths, the same fall-through and
    the same note text as a run against the gateway. It exists because the calibration
    corpus is private and cannot be served by a real deployment in CI.

    Files are keyed by whatever name they carry on disk. A request for an id that is not a
    filename falls back to a scan on each payload's own ``id`` — the corpus keys six of its
    twenty-two records under a different number than the record states, and that mismatch
    is the whole reason :attr:`FetchedRecord.record_id` exists.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0] not in ("meetings", "transcripts"):
            return httpx.Response(404, json={"detail": f"no route {path}"})

        if parts[0] == "transcripts":
            record = self._load(parts[-1])
            if record is None:
                return httpx.Response(404, json={"detail": "unknown record"})
            return httpx.Response(200, json={"segments": record.get("segments") or []})

        record = self._load(parts[1])
        if record is None:
            return httpx.Response(404, json={"detail": "unknown record"})
        if len(parts) == 2:
            return httpx.Response(200, json={k: v for k, v in record.items() if k != "segments"})
        if parts[2] == "transcript":
            return httpx.Response(200, json={"segments": record.get("segments") or []})
        return httpx.Response(404, json={"detail": f"no route {path}"})

    def _load(self, meeting_id: str) -> dict[str, Any] | None:
        direct = self.root / f"{meeting_id}.json"
        if direct.exists():
            return json.loads(direct.read_text("utf-8"))
        for candidate in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(candidate.read_text("utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(payload, dict) and str(payload.get("id")) == str(meeting_id):
                return payload
        return None
