"""The spine: a completed meeting becomes gated, per-participant context deltas, delivered.

Six stages, in this order, and the order is the design:

```
trigger → gather → GATE → render → deliver → record
```

**Gate before render, always.** Nothing is rendered for a person the gate did not authorize,
so a record that is not a meeting never produces an artifact to leak, mis-file or forward.
The alternative — render everything and filter at send — leaves a private household morning
sitting rendered on disk waiting for the one code path that forgets to check. The gate is
also the *only* authority on the recipient list: this module never mails its participant
list, it mails ``decision.allowed``.

**Idempotency is per (meeting, recipient), read from the run log.** A recipient who has a
``sent`` outcome for this meeting is skipped on re-run and recorded as such. Only ``sent``
counts — ``no_address`` and ``failed`` are retried, which is what makes a re-run after
fixing the address book do the right thing.

**The record id is the record's own.** Everything downstream — the artifact header, the
magic link's scope, the log line, the idempotency key — uses ``FetchedRecord.record_id``,
the id the payload states, never the id that was requested. When the two differ the run
records both, because a pipeline that silently substituted one for the other is how magic
links ended up pointing at the wrong meeting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from .artifact import Artifact, Recipient
from .delivery import NullDelivery
from .gate import GateDecision, PreSendGate
from .ports import (
    CompletedMeeting,
    Delivery,
    DeliveryResult,
    FetchedRecord,
    MeetingGateway,
    MeetingSource,
    ParticipantDirectory,
    Renderer,
    RunLog,
)
from .render_template import TemplateRenderer
from .runlog import RUN_LOG_SCHEMA_VERSION, MemoryRunLog
from .transcript import dominant_language, meeting_label


@dataclass(frozen=True)
class RecipientOutcome:
    recipient: Recipient
    status: str
    detail: str = ""
    reference: str = ""
    artifact: Artifact | None = None

    def to_dict(self, *, include_artifact: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "identity": self.recipient.identity,
            "display_name": self.recipient.display_name,
            "addressable": self.recipient.addressable,
            "is_creator": self.recipient.is_creator,
            "status": self.status,
            "detail": self.detail,
        }
        if self.reference:
            out["reference"] = self.reference
        if self.artifact is not None:
            out["artifact"] = (
                self.artifact.to_dict()
                if include_artifact
                else {
                    "schema_version": self.artifact.schema_version,
                    "renderer": self.artifact.renderer,
                    "language": self.artifact.language,
                    "empty": self.artifact.is_empty,
                    "sections": [
                        {"kind": s.kind, "items": len(s.items)}
                        for s in self.artifact.ordered_sections
                    ],
                }
            )
        return out


@dataclass(frozen=True)
class RunResult:
    """What one meeting's pass through the pipeline decided and did."""

    requested_id: str
    meeting_id: str
    at: str
    found: bool
    transcript_available: bool
    note: str = ""
    verdict: str = "not_evaluated"
    reasons: tuple[str, ...] = ()
    signals: dict[str, Any] = field(default_factory=dict)
    roster_source: str = "none"
    language: str = ""
    meeting_label: str = ""
    renderer: str = ""
    delivery: str = ""
    participants: tuple[Recipient, ...] = ()
    recipients: tuple[str, ...] = ()
    outcomes: tuple[RecipientOutcome, ...] = ()

    @property
    def id_matches_request(self) -> bool:
        return str(self.meeting_id) == str(self.requested_id)

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(o.artifact for o in self.outcomes if o.artifact is not None)

    @property
    def sent(self) -> tuple[RecipientOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == DeliveryResult.SENT)

    def to_log_entry(self, *, include_artifacts: bool = False) -> dict[str, Any]:
        return {
            "schema_version": RUN_LOG_SCHEMA_VERSION,
            "at": self.at,
            "requested_id": self.requested_id,
            "meeting_id": self.meeting_id,
            "id_matches_request": self.id_matches_request,
            "found": self.found,
            "transcript_available": self.transcript_available,
            "note": self.note,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "roster_source": self.roster_source,
            "signals": self.signals,
            "language": self.language,
            "meeting_label": self.meeting_label,
            "renderer": self.renderer,
            "delivery": self.delivery,
            "participants": [p.to_dict() for p in self.participants],
            "recipients": list(self.recipients),
            "outcomes": [o.to_dict(include_artifact=include_artifacts) for o in self.outcomes],
        }


class ArtifactPipeline:
    """Compose the six stages. Every collaborator is a port; none of them is optional."""

    def __init__(
        self,
        *,
        gateway: MeetingGateway,
        directory: ParticipantDirectory,
        renderer: Renderer | None = None,
        delivery: Delivery | None = None,
        run_log: RunLog | None = None,
        gate: PreSendGate | None = None,
        source: MeetingSource | None = None,
        include_artifacts_in_log: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._gateway = gateway
        self._directory = directory
        self._renderer = renderer or TemplateRenderer()
        self._delivery = delivery or NullDelivery()
        self._log = run_log or MemoryRunLog()
        self._gate = gate or PreSendGate()
        self._source = source
        self._include_artifacts = include_artifacts_in_log
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- entry points --------------------------------------------------------

    def drain(self) -> list[RunResult]:
        """Run every meeting the trigger has for us. The webhook's future entry point."""
        if self._source is None:
            raise RuntimeError("this pipeline was built without a MeetingSource")
        return [self.run(trigger) for trigger in self._source.completed()]

    def run(self, trigger: CompletedMeeting | str) -> RunResult:
        if isinstance(trigger, str):
            trigger = CompletedMeeting(meeting_id=trigger)
        record = self._gateway.fetch(trigger.meeting_id)
        result = self._process(record, trigger)
        self._log.append(result.to_log_entry(include_artifacts=self._include_artifacts))
        return result

    # -- stages --------------------------------------------------------------

    def _process(self, record: FetchedRecord, trigger: CompletedMeeting) -> RunResult:
        at = self._now().isoformat()
        if not record.found:
            return RunResult(
                requested_id=str(trigger.meeting_id),
                meeting_id=str(trigger.meeting_id),
                at=at,
                found=False,
                transcript_available=False,
                note=record.note,
                verdict="unavailable",
                delivery=self._delivery.name,
                renderer=self._renderer.name,
            )

        participants = tuple(self._directory.resolve(record, trigger))
        decision: GateDecision = self._gate.decide(record, trigger, participants)
        language = dominant_language(record)
        label = meeting_label(record, language)
        meeting_id = record.record_id
        allowed = set(decision.allowed)
        already = set(self._log.delivered_identities(meeting_id))

        outcomes: list[RecipientOutcome] = []
        for person in participants:
            if person.identity not in allowed:
                outcomes.append(
                    RecipientOutcome(
                        recipient=person,
                        status=DeliveryResult.SUPPRESSED,
                        detail=f"gate: {decision.outcome}",
                    )
                )
                continue
            artifact = self._renderer.render(
                record=record,
                recipient=person,
                participants=participants,
                meeting_id=meeting_id,
                meeting_label=label,
                language=language,
            )
            if person.identity in already:
                outcomes.append(
                    RecipientOutcome(
                        recipient=person,
                        status=DeliveryResult.DUPLICATE,
                        detail="already delivered for this record",
                        artifact=artifact,
                    )
                )
                continue
            if getattr(self._delivery, "requires_address", False) and not person.addressable:
                outcomes.append(
                    RecipientOutcome(
                        recipient=person,
                        status=DeliveryResult.NO_ADDRESS,
                        detail=f"no address for {person.display_name}",
                        artifact=artifact,
                    )
                )
                continue
            sent = self._delivery.deliver(artifact, person)
            outcomes.append(
                RecipientOutcome(
                    recipient=person,
                    status=sent.status,
                    detail=sent.detail,
                    reference=sent.reference,
                    artifact=artifact,
                )
            )
            if sent.delivered:
                already.add(person.identity)

        return RunResult(
            requested_id=str(trigger.meeting_id),
            meeting_id=str(meeting_id),
            at=at,
            found=True,
            transcript_available=record.transcript_available,
            note=record.note,
            verdict=decision.outcome,
            reasons=decision.reasons,
            signals=decision.signals,
            roster_source=decision.roster_source,
            language=language,
            meeting_label=label,
            renderer=self._renderer.name,
            delivery=self._delivery.name,
            participants=participants,
            recipients=tuple(decision.allowed),
            outcomes=tuple(outcomes),
        )


class ListSource:
    """The v0 trigger: an explicit list of completed meetings, from the CLI.

    The real trigger is the ``meeting.completed`` webhook, which will carry the workspace and
    the invitation roster alongside the id. Both are already fields on
    :class:`~vexa_artifact_pipeline.ports.CompletedMeeting`, so the webhook receiver replaces
    this class and changes nothing else.
    """

    def __init__(self, meetings: Iterable[CompletedMeeting | str]) -> None:
        self._meetings = [
            CompletedMeeting(meeting_id=m) if isinstance(m, str) else m for m in meetings
        ]

    def completed(self) -> Sequence[CompletedMeeting]:
        return tuple(self._meetings)


__all__ = ["ArtifactPipeline", "ListSource", "RecipientOutcome", "RunResult"]
