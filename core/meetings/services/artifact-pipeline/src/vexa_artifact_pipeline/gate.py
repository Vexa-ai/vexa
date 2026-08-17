"""Gate — the pre-send eligibility check, and the only authority on who may be written to.

This stage composes the ``presend-gate`` brick (``core/meetings/modules/presend-gate``)
rather than re-deciding anything. The brick answers one question — *do we have positive
evidence that two or more parties were present and talking to each other?* — and returns
``send`` · ``hold_for_creator`` · ``suppress``. The recipient list comes from the brick's
``route_recipients``, which is the single place a broadcast is authorized; a pipeline that
mails its participant list directly has bypassed the gate.

Nothing downstream re-decides. The gate exists because rendering artifacts over a real
22-record archive found two records that were **not meetings** — an hour of played-back
video audio, and a bot left running through a private household morning — and both produced
clean, sendable artifacts that nothing in the pipeline noticed.

The strongest input the gate can be given is the **invitation roster**, so it is passed
whenever the trigger has one (``roster_source="invite"``): a roster says who was *asked* to
be there, which the transcript cannot. Absence of a roster is never evidence of absence —
three real meetings in the calibration corpus carry none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:
    from presend_gate import (
        MeetingRecord,
        Outcome,
        Policy,
        Verdict,
        evaluate,
        from_transcript_payload,
        route_recipients,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment wiring, not logic
    raise ModuleNotFoundError(
        "presend_gate is not importable. The gate stage composes the presend-gate brick at "
        "core/meetings/modules/presend-gate; put its src/ on PYTHONPATH "
        "(PYTHONPATH=src:../../modules/presend-gate/src). The pipeline does not ship a "
        "fallback: there is no correct behaviour for 'send without the gate'."
    ) from exc

from .artifact import Recipient
from .ports import CompletedMeeting, FetchedRecord


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    #: Identities the gate authorizes, in participant order. Empty is a legitimate answer.
    allowed: tuple[str, ...]
    roster_source: str

    @property
    def outcome(self) -> str:
        return self.verdict.outcome.value

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self.verdict.reasons)

    @property
    def signals(self) -> dict[str, Any]:
        s = self.verdict.signals
        return {
            "segment_count": s.segment_count,
            "word_count": s.word_count,
            "dialogue_window_share": round(s.dialogue_window_share, 4),
            "alternation_rate": round(s.alternation_rate, 4),
            "monologue_ratio": round(s.monologue_ratio, 4),
            "top_speaker_share": round(s.top_speaker_share, 4),
            "substantive_speaker_count": s.substantive_speaker_count,
            "counterparty_known": s.counterparty_known,
            "counterparty_count": s.counterparty_count,
            "bot_speaker_present": s.bot_speaker_present,
            "domestic_rate": round(s.domestic_rate, 4),
            "soft_flags": list(self.verdict.soft_flags),
            "sensitive_context": self.verdict.sensitive_context,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reasons": list(self.reasons),
            "note": self.verdict.note,
            "roster_source": self.roster_source,
            "signals": self.signals,
        }


class PreSendGate:
    """The gate stage. ``policy`` is the brick's own thresholds — pass one to tune."""

    def __init__(self, policy: Policy | None = None) -> None:
        self._policy = policy

    def normalize(
        self, record: FetchedRecord, trigger: CompletedMeeting
    ) -> tuple[MeetingRecord, str]:
        payload: Mapping[str, Any] = {**record.payload, "segments": record.segments}
        roster: list[str] | None = None
        roster_source: str | None = None
        if trigger.has_invite_roster:
            roster = [
                str(p.get("name") or p.get("email") or "").strip()
                for p in trigger.invite_participants
                if str(p.get("name") or p.get("email") or "").strip()
            ]
            roster_source = "invite"
        normalized = from_transcript_payload(
            dict(payload),
            creator=trigger.creator,
            bot_names=trigger.bot_names,
            roster=roster,
            roster_source=roster_source,
        )
        return normalized, normalized.roster_source

    def decide(
        self,
        record: FetchedRecord,
        trigger: CompletedMeeting,
        participants: Sequence[Recipient],
    ) -> GateDecision:
        normalized, roster_source = self.normalize(record, trigger)
        verdict = evaluate(normalized, self._policy)
        creator = next((p for p in participants if p.is_creator), None)
        allowed = route_recipients(
            verdict,
            [p.identity for p in participants],
            creator.identity if creator else None,
        )
        return GateDecision(verdict=verdict, allowed=tuple(allowed), roster_source=roster_source)


__all__ = ["GateDecision", "Outcome", "Policy", "PreSendGate"]
