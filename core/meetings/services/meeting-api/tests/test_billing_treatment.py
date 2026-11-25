"""The billing treatment fixed at admission travels, frozen, onto the terminal system webhook.

service-authority.v1 lets an ALLOWED decision state how the deciding service bills the service it
admits (`metered` · `included` · `commitment`). meeting-api never interprets it: it freezes the
admission's value on the meeting, a continuation never changes it, and webhook.v1 carries it as
`data.meeting.billing_treatment` (null when unstated), so a downstream ledger rates a meeting by its
admission and not by the account's plan at the end.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import _repo_root
from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.service import request_bot
from meeting_api.service_authority import (
    ServiceAuthorityDecision,
    ServiceAuthorityRequest,
    run_service_authority_sweep,
)

UTC = timezone.utc
ADMITTED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)


def _admit_request() -> ServiceAuthorityRequest:
    return ServiceAuthorityRequest.admit(
        user_id=41,
        request_id="meeting-session-42:admit",
        service_identity="meeting-session-42",
        transcription_provider="none",
        active_concurrency=0,
    )


def _wire(**extra) -> dict:
    body = {
        "authority_version": "service-authority.v1",
        "decision_id": "decision-allow-42",
        "request_id": "meeting-session-42:admit",
        "service_identity": "meeting-session-42",
        "allow": True,
        "reason": "allowed",
        "decided_at": datetime.now(UTC).isoformat(),
        "stop_scope": None,
    }
    body.update(extra)
    return body


def _parse(body, *, strict=False):
    return ServiceAuthorityDecision.from_wire(
        body,
        request=_admit_request(),
        now=datetime.now(UTC),
        max_age_seconds=60,
        strict=strict,
    )


# ── the contract field ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("treatment", ["metered", "included", "commitment"])
def test_allowed_decision_carries_a_known_treatment(treatment):
    decision = _parse(_wire(billing_treatment=treatment), strict=True)
    assert decision.billing_treatment == treatment


def test_absent_treatment_is_none_and_unchanged_wire_still_parses_strictly():
    assert _parse(_wire(), strict=True).billing_treatment is None


def test_unknown_treatment_is_ignored_never_an_outage():
    assert _parse(_wire(billing_treatment="trial")).billing_treatment is None
    assert _parse(_wire(billing_treatment=7)).billing_treatment is None


def test_denied_decision_carries_no_treatment():
    decision = _parse(_wire(allow=False, reason="insufficient_balance", billing_treatment="metered"))
    assert decision.allow is False
    assert decision.billing_treatment is None


def test_constructor_refuses_a_treatment_on_a_denial():
    with pytest.raises(ValueError, match="billing_treatment"):
        replace(
            _parse(_wire()),
            allow=False,
            reason="insufficient_balance",
            billing_treatment="metered",
        )


def test_contract_golden_with_treatment_parses_strictly():
    golden = _repo_root() / "meetings" / "contracts" / "service-authority.v1" / "golden" / (
        "Decision.allow-treatment.json"
    )
    body = json.loads(golden.read_text())
    request = ServiceAuthorityRequest.admit(
        user_id=41,
        request_id=body["request_id"],
        service_identity=body["service_identity"],
        transcription_provider="none",
        active_concurrency=0,
    )
    decision = ServiceAuthorityDecision.from_wire(
        body,
        request=request,
        now=datetime.fromisoformat(body["decided_at"].replace("Z", "+00:00")),
        max_age_seconds=60,
        strict=True,
    )
    assert decision.billing_treatment == "included"


# ── frozen at admission ───────────────────────────────────────────────────────────────────────


class _Authority:
    configured = True
    mode = "enforce"

    def __init__(self, admit_treatment, continue_treatment=None):
        self.admit_treatment = admit_treatment
        self.continue_treatment = continue_treatment

    async def decide(self, request):
        action = request.action
        return ServiceAuthorityDecision(
            authority_version="service-authority.v1",
            decision_id=f"decision-{action}-{request.request_id}",
            request_id=request.request_id,
            service_identity=request.service_identity,
            allow=True,
            reason="allowed",
            decided_at=datetime.now(UTC),
            billing_treatment=(
                self.admit_treatment if action == "admit" else self.continue_treatment
            ),
        )


async def _spawn(authority):
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    result = await request_bot(
        repo,
        runtime,
        authority=authority,
        user_id=41,
        platform="google_meet",
        native_meeting_id="treatment-fixture",
        transcribe_enabled=False,
        recording_enabled=False,
        token_secret="fixture-token-secret",
    )
    return repo, runtime, result


@pytest.mark.asyncio
async def test_admission_treatment_is_frozen_and_a_continuation_never_changes_it():
    authority = _Authority("included", continue_treatment="metered")
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    assert row["data"]["service_authority"]["billing_treatment"] == "included"

    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]
    observation = await run_service_authority_sweep(
        repo, runtime, authority, now=ADMITTED_AT + timedelta(minutes=2, seconds=1),
    )
    assert observation.decisions == 2
    stored = repo._meetings[meeting["id"]]["data"]["service_authority"]
    assert stored["last_decision_id"].startswith("decision-continue-")
    assert stored["billing_treatment"] == "included"


@pytest.mark.asyncio
async def test_no_stated_treatment_stores_nothing():
    repo, _runtime, meeting = await _spawn(_Authority(None))
    assert "billing_treatment" not in repo._meetings[meeting["id"]]["data"]["service_authority"]


# ── on the terminal webhook ───────────────────────────────────────────────────────────────────


class _SystemCapture:
    def __init__(self):
        self.calls = []

    async def deliver(self, envelope, *, label=""):
        self.calls.append(envelope)


def _complete(authority_record):
    repo = InMemoryMeetingRepo()
    data = {"service_authority": authority_record} if authority_record is not None else {}
    meeting = asyncio.run(repo.create_meeting(
        user_id=17, platform="google_meet", native_meeting_id="treatment-meeting", data=data,
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid="sess-treatment"))
    sink = _SystemCapture()
    client = TestClient(
        create_app(open_callbacks=True, meeting_repo=repo, system_webhook_sink=sink),
    )
    for status, ts in (("joining", "10:00"), ("active", "10:01"), ("completed", "10:02")):
        body = {
            "connection_id": "sess-treatment",
            "status": status,
            "timestamp": f"2026-07-29T{ts}:00.000Z",
        }
        if status == "completed":
            body["completion_reason"] = "stopped"
        response = client.post("/bots/internal/callback/lifecycle", json=body)
        assert response.status_code == 200, response.text
    [envelope] = sink.calls
    assert envelope["event_type"] == "meeting.completed"
    assert envelope["event_id"]
    return envelope["data"]["meeting"]


def test_completed_webhook_carries_the_admission_treatment():
    meeting = _complete({
        "authority_version": "service-authority.v1",
        "decision_id": "decision-admit-1",
        "service_identity": "meeting-session:x",
        "billing_treatment": "commitment",
    })
    assert meeting["billing_treatment"] == "commitment"


def test_completed_webhook_says_null_when_no_treatment_was_stated():
    assert _complete(None)["billing_treatment"] is None
    assert _complete({"authority_version": "service-authority.v1"})["billing_treatment"] is None
