"""The deletion stamp meeting-api writes is api.v1's ArtifactDeletion (S12).

When the owner deletes a meeting's transcript and recordings, meeting-api keeps the row and stamps
`data.artifact_deletion` on it; agent-api and the terminal read that stamp to treat the transcript
as gone. It was an `additionalProperties` field, so a reshape here would have reappeared deleted
transcripts in chat and the live view with no gate failing. Now the shape is the sealed contract's
`ArtifactDeletion`, and this test holds the writer to it (the readers' tests read the same contract).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema

from meeting_api.collector import ports
from meeting_api.collector.fakes import InMemoryTranscriptStore

API_V1 = Path(__file__).resolve().parents[5] / "core" / "gateway" / "contracts" / "api.v1"
SCHEMA = json.loads((API_V1 / "api.schema.json").read_text())


def _validator(name: str):
    return jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": SCHEMA["components"]},
        format_checker=jsonschema.FormatChecker())


def test_the_stamp_lives_where_the_contract_says():
    data = next(a for a in SCHEMA["components"]["schemas"]["MeetingResponse"]["properties"]["data"]["anyOf"]
                if a.get("type") == "object")
    assert data["properties"][ports.ARTIFACT_DELETION_FIELD] == {"$ref": "#/components/schemas/ArtifactDeletion"}


def test_both_states_of_the_stamp_conform_to_ArtifactDeletion():
    v = _validator("ArtifactDeletion")
    for state in SCHEMA["components"]["schemas"]["ArtifactDeletion"]["properties"]["state"]["enum"]:
        v.validate(ports.deletion_stamp(state, at="2026-10-08T12:00:00Z"))
    # a pending stamp keeps the first request time across a retry
    again = ports.deletion_stamp("pending", at="2026-10-09T00:00:00Z",
                                 prior={"requested_at": "2026-10-08T12:00:00Z"})
    assert again["requested_at"] == "2026-10-08T12:00:00Z"


def test_the_store_writes_the_contract_shape_through_a_whole_delete():
    store = InMemoryTranscriptStore()
    meeting = {"user_id": 7, "status": "completed", "data": {"recordings": []}}
    store._meetings[1] = meeting
    v = _validator("ArtifactDeletion")

    asyncio.run(store.prepare_completed_artifact_deletion(7, 1))
    v.validate(meeting["data"][ports.ARTIFACT_DELETION_FIELD])
    assert meeting["data"][ports.ARTIFACT_DELETION_FIELD]["state"] == "pending"

    asyncio.run(store.finalize_completed_artifact_deletion(7, 1))
    v.validate(meeting["data"][ports.ARTIFACT_DELETION_FIELD])
    assert meeting["data"][ports.ARTIFACT_DELETION_FIELD]["state"] == "completed"
