"""A dev-only record source: meeting JSON read off disk instead of over HTTP.

The meeting API is the real source and :class:`chat_door.meetings_client.MeetingsClient` is the
real client. This exists for one situation: a dev box (or the mk-dev compose project) that has
Mailpit and the door but no meetings backend yet, where the alternative is a demo whose record
pane says only "nothing answered".

It reads the harvested corpus shape — one ``<meeting_id>.json`` per record with ``id``,
``platform``, ``data`` and ``segments`` — and it **labels itself on the page**: the note says
the record came from a local corpus, not from the API. A demo that quietly substitutes a file
for the system under test is the failure this note exists to prevent.

Enabled only when ``CHAT_DOOR_RECORDS_DIR`` is set. Unset, the door talks to the API.
"""
from __future__ import annotations

import json
from pathlib import Path

from .meetings_client import MeetingRecord

LOCAL_NOTE = "read from a local corpus file, not from the meeting API (dev source)"


class LocalCorpusRecords:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def close(self) -> None:  # parity with MeetingsClient
        return None

    def fetch(self, meeting_id: str) -> MeetingRecord:
        path = self._resolve(str(meeting_id))
        if path is None:
            return MeetingRecord(
                meeting_id=str(meeting_id),
                found=False,
                note=f"no corpus file for record {meeting_id} under {self.root} ({LOCAL_NOTE})",
            )
        payload = json.loads(path.read_text("utf-8"))
        segments = [s for s in (payload.get("segments") or []) if isinstance(s, dict)]
        return MeetingRecord(
            meeting_id=str(meeting_id),
            found=True,
            meeting={k: v for k, v in payload.items() if k != "segments"},
            segments=segments,
            transcript_available=True,
            note=LOCAL_NOTE,
        )

    def _resolve(self, meeting_id: str) -> Path | None:
        direct = self.root / f"{meeting_id}.json"
        if direct.exists():
            return direct
        # The corpus keys some files by a different id than the record states; fall back to a
        # scan on the record's own `id` so the postman's record id still resolves.
        for candidate in sorted(self.root.glob("*.json")):
            try:
                if str(json.loads(candidate.read_text("utf-8")).get("id")) == meeting_id:
                    return candidate
            except (ValueError, OSError):
                continue
        return None
