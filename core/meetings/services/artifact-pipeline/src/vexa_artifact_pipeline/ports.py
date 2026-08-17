"""The six seams — every collaborator the pipeline talks to, as a Protocol.

:class:`~vexa_artifact_pipeline.pipeline.ArtifactPipeline` is written against these and
nothing else, so the tests drive the SHIPPED spine with in-process fakes and production
swaps an implementation without the spine knowing. Each port exists because the v0 choice
behind it is a shortcut that must stay reversible:

* **:class:`MeetingSource`** — v0 is a CLI holding a list of ids. The real trigger is the
  ``meeting.completed`` webhook, which will also carry the invite roster (the mailroom's
  binding already stores it) and the workspace. The port is "hand me completed meetings",
  which a webhook receiver, a poller, or a queue consumer all answer.
* **:class:`MeetingGateway`** — the record is fetched over the public REST surface with an
  API key, exactly as a customer would. The pipeline never imports ``meeting_api``
  (``gate:graph-py``) and never touches the meetings database.
* **:class:`ParticipantDirectory`** — who this meeting's artifacts are *for*, and how to
  reach them. v0 reads the roster the record carries plus an operator-supplied address
  book; Stage 1 reads the invitation, which is the authoritative roster.
* **:class:`Renderer`** — the deterministic template renderer ships; the model-driven one
  is a stub, because it needs the workspace's BYOT model route and that decision is open.
* **:class:`Delivery`** — v0 hands the artifact to the chat-door postman through its CLI,
  which is where the magic-link signing key lives. A file sink and a recording fake are the
  other two implementations.
* **:class:`RunLog`** — append-only. It is both the audit surface and the idempotency
  oracle: what already went out is read back from the same stream that recorded it, so
  there is no second store to disagree with the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .artifact import Artifact, Recipient


@dataclass(frozen=True)
class CompletedMeeting:
    """One trigger event: a meeting the pipeline should now produce artifacts for.

    Everything past ``meeting_id`` is what the trigger *happens to know*. A CLI knows the
    id and whatever the operator typed; the webhook will know the workspace and, through
    the mailroom binding, the invitation's roster — which is the strongest evidence the
    pre-send gate can be given (``roster_source="invite"``).
    """

    meeting_id: str
    workspace_id: str | None = None
    creator: str | None = None
    creator_email: str | None = None
    #: Invite roster, when the trigger has one: ``{"email": ..., "name": ...}`` entries.
    #: Empty means "the trigger does not know", never "there was nobody".
    invite_participants: tuple[Mapping[str, Any], ...] = ()
    bot_names: tuple[str, ...] = ()

    @property
    def has_invite_roster(self) -> bool:
        return bool(self.invite_participants)


@dataclass
class FetchedRecord:
    """What the gather stage returns: the record payload, or an honest statement of why not.

    ``note`` is never left implicit. "The API did not answer" and "the meeting has an empty
    transcript" are different facts and the pipeline reacts differently to them; a record
    that silently reads as empty is the failure this field exists to prevent.
    """

    requested_id: str
    found: bool
    payload: dict[str, Any] = field(default_factory=dict)
    segments: list[dict[str, Any]] = field(default_factory=list)
    transcript_available: bool = False
    note: str = ""

    @property
    def record_id(self) -> str:
        """The record's OWN id. Falls back to the requested id only when the payload has
        none — and that fallback is recorded, because it is exactly how a wrong magic link
        was minted once."""
        stated = self.payload.get("id")
        return str(stated) if stated not in (None, "") else str(self.requested_id)

    @property
    def id_matches_request(self) -> bool:
        return str(self.record_id) == str(self.requested_id)


@dataclass(frozen=True)
class DeliveryResult:
    """One (artifact, recipient) attempt.

    ``sent`` is the only terminal success — it is what idempotency reads back. ``no_address``
    is a first-class outcome, not an error: a participant the record names but that we
    cannot reach is a real, common state (the corpus roster is display names) and the
    artifact still exists and is still recorded.
    """

    status: str
    detail: str = ""
    reference: str = ""

    SENT = "sent"
    NO_ADDRESS = "no_address"
    FAILED = "failed"
    DUPLICATE = "skipped_duplicate"
    SUPPRESSED = "suppressed"

    @property
    def delivered(self) -> bool:
        return self.status == self.SENT

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "reference": self.reference}


class MeetingSource(Protocol):
    """The trigger. ``completed()`` yields meetings ready for artifacts, oldest first."""

    def completed(self) -> Iterable[CompletedMeeting]: ...


class MeetingGateway(Protocol):
    """The meeting API, as a consumer sees it."""

    def fetch(self, meeting_id: str) -> FetchedRecord: ...


class ParticipantDirectory(Protocol):
    """Who the artifacts are for, and how to reach each of them."""

    def resolve(self, record: FetchedRecord, trigger: CompletedMeeting) -> Sequence[Recipient]: ...


class Renderer(Protocol):
    """Build one person's context delta. ``name`` is recorded on every artifact and run."""

    name: str

    def render(
        self,
        *,
        record: FetchedRecord,
        recipient: Recipient,
        participants: Sequence[Recipient],
        meeting_id: str,
        meeting_label: str,
        language: str,
    ) -> Artifact: ...


class Delivery(Protocol):
    """Where a rendered artifact goes. Implementations must be safe to call twice —
    the pipeline guards duplicates, but a crash between send and record is possible."""

    name: str

    def deliver(self, artifact: Artifact, recipient: Recipient) -> DeliveryResult: ...


class RunLog(Protocol):
    """Append-only record of every run, and the idempotency oracle read back from it."""

    def append(self, entry: Mapping[str, Any]) -> None: ...

    def delivered_identities(self, meeting_id: str) -> frozenset[str]: ...
