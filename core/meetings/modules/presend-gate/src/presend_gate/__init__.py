"""presend-gate — decide whether a record may be emailed as an artifact, before it is.

    from presend_gate import from_transcript_payload, gate

    record = from_transcript_payload(payload, creator="a@b.com", bot_names=["Vexa"])
    verdict, recipients = gate(record, participants=invite_emails)
    if recipients:
        send_artifact(recipients, verdict.note)

Three outcomes, never two: `send` · `hold_for_creator` · `suppress`. Uncertainty routes
to `hold_for_creator` — the artifact still reaches the person who convened the meeting,
and reaches nobody else.
"""

from .policy import Outcome, Policy, Verdict, evaluate, gate, route_recipients
from .record import MeetingRecord, Segment, from_transcript_payload
from .signals import Signals, measure

__all__ = [
    "MeetingRecord",
    "Outcome",
    "Policy",
    "Segment",
    "Signals",
    "Verdict",
    "evaluate",
    "from_transcript_payload",
    "gate",
    "measure",
    "route_recipients",
]
