"""The mailroom's four ports — every collaborator the service talks to, as a Protocol.

The service (``service.Mailroom``) is written against these and nothing else, so the tests drive
the SHIPPED logic with in-process fakes (``tests/conftest.py``) and production swaps in the
adapters (``adapters.py``) without the logic knowing. Two of the four exist specifically to make a
v0 decision reversible:

* **``MailSource``** — Mailpit is the dev inbound mailbox and NOT the destination. The port is
  "give me messages newer than this cursor, as RFC-822 bytes", which is exactly what an IMAP
  ``FETCH``, an inbound-SMTP hook writing a maildir, or an SES/Postmark webhook can answer.
  Nothing above this line knows Mailpit exists.
* **``MeetingApi``** — the control plane is consumed over its public REST surface (``POST``,
  ``PATCH``, ``DELETE /meetings``), never by reaching into meeting-api's modules. The port is the
  three calls the mailroom makes, so a unit test asserts the exact intent without a control plane.

``BindingStore`` holds the series↔meeting binding + the resume cursor; ``NoticeSink`` records the
"we could not act on this" concept. v0 writes a notice to a log/queue and sends NOTHING — the
fail-safe rule is no group effect and no broadcast, and an outbound notice email is Stage 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence


@dataclass(frozen=True)
class MailMessage:
    """One inbound message: an opaque id, its arrival stamp, and the raw RFC-822 bytes."""
    id: str
    created: str          # ISO-8601; the cursor is ordered on this
    raw: bytes


@dataclass
class Binding:
    """One calendar series bound to one planned meeting row.

    ``uid`` is the ICS series identity and ``sequence`` the highest SEQUENCE acted on — together
    they are the idempotency key (the same invitation delivered twice, or re-read after a restart,
    is a no-op). ``meeting_id`` is the control plane's row id, which is what an update PATCHes and
    a cancel DELETEs.
    """
    workspace_id: str
    uid: str
    meeting_id: Optional[int] = None
    sequence: int = -1
    state: str = "active"                 # active | cancelled
    recurring: bool = False
    scheduled_at: Optional[str] = None
    meeting_url: Optional[str] = None
    platform: Optional[str] = None
    native_meeting_id: Optional[str] = None
    title: Optional[str] = None
    invited_address: Optional[str] = None
    last_message_id: Optional[str] = None
    # The series' own definition, kept so the next occurrence can be re-derived without the
    # original message: a recurring invitation is sent ONCE and must keep producing occurrences.
    rrule: Optional[str] = None
    series_start: Optional[str] = None
    # The roster from the invitation (email · name · role · partstat). The control plane has no
    # participant surface yet, so this is where the invite's identities live today.
    participants: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.workspace_id, self.uid)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Binding":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Notice:
    """A message that produced no group effect, and why. v0: recorded, never sent.

    ``to`` is where the explanation WOULD go when notices become outbound (Stage 1) — the
    organizer, falling back to the sender. It is recorded now so the queue that Stage 1 drains is
    already addressed, and so the v0 log answers "who would we have told".
    """
    message_id: str
    reason: str
    detail: str = ""
    to: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    uid: Optional[str] = None
    at: Optional[str] = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class MailSource(Protocol):
    """The inbound mailbox. ``fetch_new`` returns messages in ARRIVAL order (oldest first)."""

    async def fetch_new(self, *, since: Optional[str], limit: int) -> Sequence[MailMessage]:
        ...


class MeetingApi(Protocol):
    """The control plane's planned-meeting surface — the three calls the mailroom makes."""

    async def create_planned_meeting(self, *, workspace_id: Optional[str], meeting_url: str,
                                     title: Optional[str], scheduled_at: Optional[str],
                                     auto_join: bool = True) -> dict:
        """``POST /meetings`` → the created row (``{id, status, ...}``)."""

    async def update_planned_meeting(self, meeting_id: int, **fields: Any) -> dict:
        """``PATCH /meetings/{id}`` with only the changed fields → the updated row."""

    async def cancel_planned_meeting(self, meeting_id: int) -> bool:
        """``DELETE /meetings/{id}`` → True when the row is gone (404/409 → False, never raises)."""


class BindingStore(Protocol):
    """Durable series↔meeting bindings + the resume cursor."""

    async def get(self, workspace_id: str, uid: str) -> Optional[Binding]: ...
    async def put(self, binding: Binding) -> None: ...
    async def all(self) -> Sequence[Binding]: ...
    async def cursor(self) -> Optional[str]: ...
    async def set_cursor(self, value: Optional[str], seen: Sequence[str]) -> None: ...
    async def seen(self) -> Sequence[str]: ...


class NoticeSink(Protocol):
    """Where "we could not act on this" goes. v0 = an append-only log; Stage 1 = an email."""

    async def record(self, notice: Notice) -> None: ...
    async def recent(self, limit: int = 50) -> Sequence[Notice]: ...
