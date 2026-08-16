"""Shared eval fixtures — in-process fakes for the mailbox and the control plane.

Autonomous, the repo idiom: no docker, no network, no Mailpit, no gateway. ``Mailroom`` is written
against ports, so these fakes drive the SHIPPED decision logic and record exactly what it asked
the control plane to do — which is what the corpus table asserts.

``envelope()`` wraps a bare ``.ics`` in the minimal RFC-822 message a calendar client sends. The
same helper wraps the in-repo corpus and any external ICS corpus, so both run through one harness.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

from vexa_mailroom import Mailroom, MemoryStore
from vexa_mailroom.ports import MailMessage

WORKSPACE_ADDRESS = "mk-dev@dev.vexa.ai"
WORKSPACE_ID = "ws-mk-dev"
# Pinned clock — the corpus carries absolute dates so every expectation is deterministic.
NOW = datetime(2026, 8, 16, 9, 0, 0, tzinfo=timezone.utc)

FIXTURES = Path(__file__).parent / "fixtures"
ICS_DIR = FIXTURES / "ics"
EML_DIR = FIXTURES / "eml"


def envelope(ics: str, *, to: str = WORKSPACE_ADDRESS, sender: str = "organizer@example.com",
             subject: str = "Invitation", message_id: str = "<msg@example.com>",
             method: Optional[str] = None) -> bytes:
    """A bare .ics → the message a calendar client actually sends (text/calendar part)."""
    method_param = f"; method={method}" if method else ""
    return (
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        f"Content-Type: text/calendar; charset=UTF-8{method_param}\r\n"
        "\r\n"
        f"{ics}"
    ).encode("utf-8")


def read_ics(name: str) -> str:
    return (ICS_DIR / name).read_text("utf-8")


def read_eml(name: str) -> bytes:
    return (EML_DIR / name).read_bytes()


@dataclass
class FakeMailSource:
    """A mailbox that hands out messages newer than the cursor, oldest first (Mailpit's contract)."""
    messages: list[MailMessage] = field(default_factory=list)
    fetches: list[Optional[str]] = field(default_factory=list)

    def add(self, raw: bytes, *, id: Optional[str] = None, created: Optional[str] = None) -> MailMessage:
        n = len(self.messages) + 1
        m = MailMessage(id=id or f"msg-{n:03d}",
                        created=created or f"2026-08-16T09:{n:02d}:00Z",
                        raw=raw)
        self.messages.append(m)
        return m

    async def fetch_new(self, *, since: Optional[str], limit: int) -> Sequence[MailMessage]:
        self.fetches.append(since)
        rows = [m for m in self.messages if since is None or (m.created or "") > since]
        return sorted(rows, key=lambda m: (m.created, m.id))[:limit]


@dataclass
class FakeMeetingApi:
    """Records every control-plane call; answers like the real ``POST/PATCH/DELETE /meetings``."""
    calls: list[tuple[str, dict]] = field(default_factory=list)
    rows: dict[int, dict] = field(default_factory=dict)
    next_id: int = 1
    create_error: Optional[str] = None
    update_error: Optional[str] = None
    cancel_result: bool = True

    async def create_planned_meeting(self, **kwargs) -> dict:
        self.calls.append(("create", dict(kwargs)))
        if self.create_error:
            return {"error": self.create_error}
        row = {"id": self.next_id, "status": "scheduled", **kwargs}
        self.rows[self.next_id] = row
        self.next_id += 1
        return row

    async def update_planned_meeting(self, meeting_id: int, **fields: Any) -> dict:
        self.calls.append(("update", {"meeting_id": meeting_id, **fields}))
        if self.update_error:
            return {"error": self.update_error}
        row = self.rows.setdefault(meeting_id, {"id": meeting_id})
        row.update(fields)
        return row

    async def cancel_planned_meeting(self, meeting_id: int) -> bool:
        self.calls.append(("cancel", {"meeting_id": meeting_id}))
        if self.cancel_result:
            self.rows.pop(meeting_id, None)
        return self.cancel_result

    def of(self, verb: str) -> list[dict]:
        return [payload for v, payload in self.calls if v == verb]


@pytest.fixture
def source() -> FakeMailSource:
    return FakeMailSource()


@pytest.fixture
def meetings() -> FakeMeetingApi:
    return FakeMeetingApi()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def mailroom(source: FakeMailSource, meetings: FakeMeetingApi, store: MemoryStore) -> Mailroom:
    return Mailroom(source=source, meetings=meetings, store=store, notices=store,
                    workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)


@pytest.fixture
def external_corpus() -> Optional[Path]:
    """The out-of-repo ICS corpus, when one is pointed at (``MAILROOM_ICS_CORPUS``)."""
    raw = os.getenv("MAILROOM_ICS_CORPUS")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None
