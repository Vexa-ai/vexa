"""artifact-pipeline — a completed meeting becomes gated, per-participant context deltas.

The spine the meeting-knowledge product was missing: every other piece existed (the record,
the pre-send gate, the postman, the chat door) and nothing turned a finished meeting into
artifacts. This service is that arrow — ``workspace × meeting → artifacts`` — where an
artifact is *the rendered context delta for ONE person*.

    from vexa_artifact_pipeline import (
        ArtifactPipeline, HttpMeetingGateway, RosterDirectory, TemplateRenderer,
        FileDelivery, JsonlRunLog, CompletedMeeting,
    )

    pipeline = ArtifactPipeline(
        gateway=HttpMeetingGateway("http://localhost:18056", api_key=key),
        directory=RosterDirectory(address_book={"Marvin Hanke": "marvin@example.test"}),
        renderer=TemplateRenderer(),
        delivery=FileDelivery("out/artifacts"),
        run_log=JsonlRunLog("out/runs.jsonl"),
    )
    result = pipeline.run(CompletedMeeting(meeting_id="12615", creator="Dmitry Grankin"))

Six stages, each behind a port so the v0 shortcut behind it stays reversible — trigger,
gather, gate, render, deliver, record. Read :mod:`.ports` for the seams and :mod:`.pipeline`
for the order they run in and why.
"""

from .artifact import ARTIFACT_SCHEMA_VERSION, Artifact, Recipient, Section, slugify
from .delivery import CommandDelivery, FileDelivery, NullDelivery, postman_delivery
from .directory import RosterDirectory
from .gate import GateDecision, PreSendGate
from .gateway import CorpusTransport, HttpMeetingGateway
from .labels import KIND_ORDER, Vocabulary, vocabulary_for
from .pipeline import ArtifactPipeline, ListSource, RecipientOutcome, RunResult
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
from .render_llm import LlmRenderer
from .render_template import TemplateRenderer
from .runlog import RUN_LOG_SCHEMA_VERSION, JsonlRunLog, MemoryRunLog

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "RUN_LOG_SCHEMA_VERSION",
    "Artifact",
    "ArtifactPipeline",
    "CommandDelivery",
    "CompletedMeeting",
    "CorpusTransport",
    "Delivery",
    "DeliveryResult",
    "FetchedRecord",
    "FileDelivery",
    "GateDecision",
    "HttpMeetingGateway",
    "JsonlRunLog",
    "KIND_ORDER",
    "ListSource",
    "LlmRenderer",
    "MeetingGateway",
    "MeetingSource",
    "MemoryRunLog",
    "NullDelivery",
    "ParticipantDirectory",
    "PreSendGate",
    "Recipient",
    "RecipientOutcome",
    "Renderer",
    "RosterDirectory",
    "RunLog",
    "RunResult",
    "Section",
    "TemplateRenderer",
    "Vocabulary",
    "postman_delivery",
    "slugify",
    "vocabulary_for",
]
