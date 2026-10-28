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


# ── the readers: the upload's session lookup reads the stamp through the same contract ──────────

GOLDEN = API_V1 / "golden"


def test_the_reader_knows_exactly_the_contracts_states():
    schema = SCHEMA["components"]["schemas"]["ArtifactDeletion"]
    assert tuple(schema["properties"]["state"]["enum"]) == ports.ARTIFACT_DELETION_STATES
    assert schema["required"] == ["state"]


def test_every_golden_deletion_reads_as_erased():
    v = _validator("ArtifactDeletion")
    goldens = sorted(GOLDEN.glob("MeetingResponse.*.json"))
    stamped = [g for g in goldens if ports.ARTIFACT_DELETION_FIELD in (json.loads(g.read_text()).get("data") or {})]
    assert {g.name for g in stamped} >= {"MeetingResponse.deleted.json", "MeetingResponse.deleting.json"}
    for g in stamped:
        data = json.loads(g.read_text())["data"]
        v.validate(data[ports.ARTIFACT_DELETION_FIELD])
        assert ports.meeting_is_erased(data), g.name
    for g in set(goldens) - set(stamped):
        assert not ports.meeting_is_erased(json.loads(g.read_text()).get("data")), g.name


def test_a_stamp_of_another_shape_still_reads_as_erased_and_is_reported(capsys):
    for odd in ({}, {"state": "gone"}, True, "completed", ["pending"]):
        assert ports.meeting_is_erased({ports.ARTIFACT_DELETION_FIELD: odd})
    assert "artifact_deletion_unreadable" in capsys.readouterr().out
    for none in (None, {}, {"recordings": []}, {ports.ARTIFACT_DELETION_FIELD: None}, "not a dict"):
        assert not ports.meeting_is_erased(none)


class _Rows:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _Db:
    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, _stmt):
        return _Rows(self._rows.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _rows(meeting_data):
    from types import SimpleNamespace

    return (SimpleNamespace(meeting_id=1, session_uid="conn-a"), SimpleNamespace(id=1, data=meeting_data))


def test_an_upload_finds_no_session_on_a_meeting_whose_golden_says_deleted():
    """The production adapter's session rule, over the rows its two queries return."""
    from meeting_api.recordings.adapters import upload_session

    assert upload_session(*_rows({"recordings": []})) == {"meeting_id": 1, "session_uid": "conn-a"}
    assert upload_session(_rows({})[0], None) is None
    for name in ("MeetingResponse.deleted.json", "MeetingResponse.deleting.json"):
        assert upload_session(*_rows(json.loads((GOLDEN / name).read_text())["data"])) is None, name


def test_find_session_applies_that_rule():
    """The same, through SqlAlchemyRecordingRepo.find_session over a scripted session (needs
    SQLAlchemy, which the service image carries and this lane may not)."""
    import pytest

    pytest.importorskip("sqlalchemy")
    from meeting_api.recordings.adapters import SqlAlchemyRecordingRepo

    def lookup(data):
        return asyncio.run(SqlAlchemyRecordingRepo(lambda: _Db(list(_rows(data)))).find_session("conn-a"))

    assert lookup({"recordings": []}) == {"meeting_id": 1, "session_uid": "conn-a"}
    assert lookup(json.loads((GOLDEN / "MeetingResponse.deleted.json").read_text())["data"]) is None
