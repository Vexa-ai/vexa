"""Acceptance oracle for the opt-in generic service-authority seam (#988).

The fixture owns no hosted policy.  It proves only that meeting-api asks a
versioned authority before effects, preserves the decision identity, and
applies a continuation stop exactly once at a one-minute boundary.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.service import request_bot
from meeting_api.service_authority import (
    AllowAllServiceAuthority,
    HttpServiceAuthority,
    ServiceAuthorityConfig,
    ServiceAuthorityDecision,
    ServiceAuthorityDenied,
    ServiceAuthorityRequest,
    ServiceAuthorityUnavailable,
    build_service_authority_from_env,
    run_service_authority_sweep,
)
from meeting_api.webhooks.delivery import clean_meeting_data


UTC = timezone.utc
ADMITTED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)


class RecordingAuthority:
    configured = True
    mode = "enforce"

    def __init__(
        self,
        *,
        admit: ServiceAuthorityDecision | Exception | None = None,
        continuation: ServiceAuthorityDecision | Exception | None = None,
        mode: str = "enforce",
    ) -> None:
        self.mode = mode
        self.admit = admit or _decision(allow=True, action="admit")
        self.continuation = continuation or _decision(
            allow=True,
            action="continue",
        )
        self.requests: list[ServiceAuthorityRequest] = []

    async def decide(
        self,
        request: ServiceAuthorityRequest,
    ) -> ServiceAuthorityDecision:
        self.requests.append(request)
        result = self.admit if request.action == "admit" else self.continuation
        if isinstance(result, Exception):
            raise result
        return replace(
            result,
            request_id=request.request_id,
            service_identity=request.service_identity,
            enforced=self.mode == "enforce",
        )


def _decision(
    *,
    allow: bool,
    action: str,
    reason: str = "allowed",
    decision_id: str | None = None,
    stop_scope: str | None = None,
) -> ServiceAuthorityDecision:
    return ServiceAuthorityDecision(
        authority_version="service-authority.v1",
        decision_id=decision_id or f"decision-{action}-{reason}",
        request_id="fixture-request",
        service_identity="fixture-service",
        allow=allow,
        reason=reason,
        decided_at=datetime.now(UTC),
        stop_scope=stop_scope,
    )


async def _spawn(
    authority,
    *,
    repo: InMemoryMeetingRepo | None = None,
    runtime: FakeRuntimeClient | None = None,
):
    repo = repo or InMemoryMeetingRepo()
    runtime = runtime or FakeRuntimeClient()
    result = await request_bot(
        repo,
        runtime,
        authority=authority,
        user_id=41,
        platform="google_meet",
        native_meeting_id="authority-fixture",
        transcribe_enabled=False,
        recording_enabled=False,
        token_secret="fixture-token-secret",
    )
    return repo, runtime, result


@pytest.mark.asyncio
async def test_unconfigured_allow_all_preserves_stock_spawn() -> None:
    repo, runtime, result = await _spawn(AllowAllServiceAuthority())

    assert result["status"] == "requested"
    assert len(repo._meetings) == 1
    assert len(runtime.specs) == 1
    authority = repo._meetings[result["id"]]["data"]["service_authority"]
    assert authority["authority_version"] == "service-authority.v1"
    assert authority["mode"] == "unconfigured"

    row = repo._meetings[result["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]
    configured_later = RecordingAuthority(
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            stop_scope="billable_service",
        ),
    )
    observation = await run_service_authority_sweep(
        repo,
        runtime,
        configured_later,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=1),
    )
    assert observation.decisions == 0
    assert configured_later.requests == []
    assert runtime.deleted == []


@pytest.mark.asyncio
async def test_frozen_service_identity_survives_privacy_safe_completion_projection() -> None:
    authority = RecordingAuthority()
    repo, _runtime, meeting = await _spawn(authority)
    stored = repo._meetings[meeting["id"]]["data"]

    projected = clean_meeting_data(stored)

    assert projected["service_authority"]["service_identity"].startswith(
        "meeting-session:",
    )
    assert projected["service_authority"]["decision_id"].startswith(
        "decision-admit-",
    )
    serialized = json.dumps(projected)
    assert "fixture-authority-secret" not in serialized
    assert "transcription_service_url" not in serialized
    assert "transcription_service_token" not in serialized


@pytest.mark.asyncio
async def test_deny_happens_before_any_meeting_or_runtime_effect() -> None:
    authority = RecordingAuthority(
        admit=_decision(
            allow=False,
            action="admit",
            reason="opaque_limit",
            decision_id="decision-deny-41",
        ),
    )
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(ServiceAuthorityDenied) as caught:
        await _spawn(authority, repo=repo, runtime=runtime)

    assert caught.value.reason == "opaque_limit"
    assert caught.value.decision_id == "decision-deny-41"
    assert repo._meetings == {}
    assert runtime.specs == []

    outbound = authority.requests[0].to_wire()
    assert set(outbound) == {
        "user_id",
        "action",
        "request_id",
        "service_identity",
        "service_mode",
        "transcription_provider",
        "lifecycle_contract_version",
        "active_concurrency",
    }
    forbidden = {
        "email",
        "stripe_customer_id",
        "price",
        "balance",
        "transcription_url",
        "credential",
        "secret",
    }
    assert forbidden.isdisjoint(outbound)


@pytest.mark.asyncio
async def test_configured_unavailable_fails_before_effects() -> None:
    authority = RecordingAuthority(
        admit=ServiceAuthorityUnavailable("fixture authority unavailable"),
    )
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(ServiceAuthorityUnavailable):
        await _spawn(authority, repo=repo, runtime=runtime)

    assert repo._meetings == {}
    assert runtime.specs == []


def test_http_deny_is_typed_and_preserves_opaque_identity() -> None:
    authority = RecordingAuthority(
        admit=_decision(
            allow=False,
            action="admit",
            reason="opaque_limit",
            decision_id="decision-http-deny-41",
        ),
    )
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(
        meeting_repo=repo,
        runtime=runtime,
        service_authority=authority,
        token_secret="fixture-token-secret",
    ))

    response = client.post(
        "/bots",
        headers={"x-user-id": "41"},
        json={
            "platform": "google_meet",
            "native_meeting_id": "authority-http-deny",
            "transcribe_enabled": False,
            "recording_enabled": False,
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "service_not_allowed",
            "reason": "opaque_limit",
            "decision_id": "decision-http-deny-41",
        },
    }
    assert repo._meetings == {}
    assert runtime.specs == []


@pytest.mark.asyncio
async def test_http_adapter_signs_exact_bytes_and_binds_fresh_response() -> None:
    secret = "fixture-authority-secret"
    captured: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        captured.append(body)
        timestamp = request.headers["x-webhook-timestamp"]
        expected = hmac.new(
            secret.encode(),
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-webhook-signature"] == f"sha256={expected}"
        payload = json.loads(body)
        return httpx.Response(
            200,
            json={
                "authority_version": "service-authority.v1",
                "decision_id": "decision-http-1",
                "request_id": payload["request_id"],
                "service_identity": payload["service_identity"],
                "allow": True,
                "reason": "allowed",
                "decided_at": datetime.now(UTC).isoformat(),
                "stop_scope": None,
            },
        )

    config = ServiceAuthorityConfig(
        url="http://service-authority:3000/api/internal/service-authority",
        secret=secret,
        timeout_seconds=2,
        response_max_age_seconds=30,
        mode="enforce",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        authority = HttpServiceAuthority(config, client=client)
        request = ServiceAuthorityRequest.admit(
            user_id=41,
            request_id="request-http-1",
            service_identity="meeting-session-http-1",
            transcription_provider="customer",
            active_concurrency=0,
        )
        result = await authority.decide(request)

    assert result.allow is True
    assert result.request_id == request.request_id
    assert result.service_identity == request.service_identity
    assert captured == [request.to_json_bytes()]


@pytest.mark.asyncio
async def test_http_adapter_rejects_stale_or_cross_bound_response() -> None:
    request = ServiceAuthorityRequest.admit(
        user_id=41,
        request_id="request-http-2",
        service_identity="meeting-session-http-2",
        transcription_provider="none",
        active_concurrency=0,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "authority_version": "service-authority.v1",
                "decision_id": "decision-stale",
                "request_id": "another-request",
                "service_identity": request.service_identity,
                "allow": True,
                "reason": "allowed",
                "decided_at": (
                    datetime.now(UTC) - timedelta(minutes=5)
                ).isoformat(),
            },
        )

    config = ServiceAuthorityConfig(
        url="http://service-authority:3000/api/internal/service-authority",
        secret="fixture-secret",
        timeout_seconds=2,
        response_max_age_seconds=30,
        mode="enforce",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ServiceAuthorityUnavailable):
            await HttpServiceAuthority(config, client=client).decide(request)


@pytest.mark.asyncio
async def test_http_adapter_rejects_denied_continuation_without_stop_scope() -> None:
    request = ServiceAuthorityRequest.continuation(
        user_id=41,
        request_id="request-http-stop",
        service_identity="meeting-session-http-stop",
        transcription_provider="none",
        active_concurrency=1,
        admitted_at=ADMITTED_AT,
        boundary_at=ADMITTED_AT + timedelta(minutes=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "authority_version": "service-authority.v1",
                "decision_id": "decision-missing-stop",
                "request_id": request.request_id,
                "service_identity": request.service_identity,
                "allow": False,
                "reason": "opaque_limit",
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )

    config = ServiceAuthorityConfig(
        url="http://service-authority:3000/api/internal/service-authority",
        secret="fixture-secret",
        timeout_seconds=2,
        response_max_age_seconds=30,
        mode="enforce",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ServiceAuthorityUnavailable):
            await HttpServiceAuthority(config, client=client).decide(request)


@pytest.mark.asyncio
async def test_active_boundary_deny_tears_down_once_and_restart_replays_none() -> None:
    authority = RecordingAuthority(
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-stop-41",
            stop_scope="billable_service",
        ),
    )
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]

    observed = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=1),
    )
    replay = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=20),
    )

    assert observed.decisions == 1
    assert observed.teardowns_confirmed == 1
    assert replay.decisions == 0
    assert replay.teardowns_confirmed == 0
    assert len(runtime.deleted) == 1
    persisted = row["data"]["service_authority"]
    assert persisted["last_decision_id"] == "decision-stop-41"
    assert persisted["last_boundary_at"] == (
        ADMITTED_AT + timedelta(minutes=1)
    ).isoformat()
    assert persisted["teardown_confirmed"] is True


@pytest.mark.asyncio
async def test_active_boundary_untracked_workload_stays_pending_then_recovers() -> None:
    authority = RecordingAuthority(
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-stop-absent",
            stop_scope="billable_service",
        ),
    )
    runtime = FakeRuntimeClient(workloads={})
    repo, _runtime, meeting = await _spawn(
        authority,
        runtime=runtime,
    )
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]

    observed = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=1),
    )
    pending_after_untracked = dict(
        row["data"]["service_authority"],
    )
    replay = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=20),
    )
    pending_after_replay = dict(row["data"]["service_authority"])
    runtime._workloads[row["bot_container_id"]] = {
        "workloadId": row["bot_container_id"],
        "state": "running",
    }
    recovered = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=2, seconds=2),
    )
    inert = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=2, seconds=20),
    )

    assert observed.decisions == 1
    assert observed.teardowns_confirmed == 0
    assert observed.faults == 1
    assert pending_after_untracked["teardown_confirmed"] is False
    assert pending_after_untracked["teardown_claim_id"]
    assert replay.decisions == 0
    assert replay.teardowns_confirmed == 0
    assert pending_after_replay["teardown_confirmed"] is False
    assert (
        pending_after_replay["teardown_claim_id"]
        == pending_after_untracked["teardown_claim_id"]
    )
    assert recovered.decisions == 0
    assert recovered.teardowns_confirmed == 1
    assert recovered.faults == 0
    assert inert.decisions == 0
    assert inert.teardowns_confirmed == 0
    assert len(runtime.deleted) == 1
    assert row["data"]["service_authority"]["teardown_confirmed"] is True


@pytest.mark.asyncio
async def test_active_boundary_unavailable_fails_closed_and_stops() -> None:
    authority = RecordingAuthority(
        continuation=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]

    observed = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=1),
    )

    persisted = row["data"]["service_authority"]
    assert observed.decisions == 1
    assert observed.teardowns_confirmed == 1
    assert observed.faults == 1
    assert persisted["reason"] == "service_authority_unavailable"
    assert persisted["stop_scope"] == "billable_service"
    assert persisted["teardown_confirmed"] is True


@pytest.mark.asyncio
async def test_two_sweep_workers_apply_one_claimed_teardown() -> None:
    class SlowRuntime(FakeRuntimeClient):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def delete_workload(self, workload_id: str) -> None:
            self.entered.set()
            await self.release.wait()
            await super().delete_workload(workload_id)

    authority = RecordingAuthority(
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-concurrent-stop",
            stop_scope="billable_service",
        ),
    )
    runtime = SlowRuntime()
    repo, _runtime, meeting = await _spawn(authority, runtime=runtime)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]
    observed_at = ADMITTED_AT + timedelta(minutes=1, seconds=1)

    first_task = asyncio.create_task(run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=observed_at,
    ))
    await runtime.entered.wait()
    second = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=observed_at,
    )
    runtime.release.set()
    first = await first_task

    assert first.teardowns_confirmed == 1
    assert second.teardowns_confirmed == 0
    assert runtime.deleted == [row["bot_container_id"]]
    assert row["data"]["service_authority"]["teardown_confirmed"] is True


@pytest.mark.asyncio
async def test_expired_teardown_claim_recovers_after_worker_crash() -> None:
    authority = RecordingAuthority(
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-crash-stop",
            stop_scope="billable_service",
        ),
    )
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]
    first_boundary = ADMITTED_AT + timedelta(minutes=1)
    decision = replace(
        authority.continuation,
        request_id=f"{row['data']['service_authority']['service_identity']}"
        f":continue:{first_boundary.isoformat()}",
        service_identity=row["data"]["service_authority"]["service_identity"],
    )
    request = ServiceAuthorityRequest.continuation(
        user_id=row["user_id"],
        request_id=decision.request_id,
        service_identity=decision.service_identity,
        transcription_provider="none",
        active_concurrency=1,
        admitted_at=ADMITTED_AT,
        boundary_at=first_boundary,
    )
    assert await repo.record_service_authority_decision(
        meeting_id=row["id"],
        request=request,
        decision=decision,
    )
    crashed_claim = await repo.claim_service_authority_teardown(
        meeting_id=row["id"],
        claim_id="crashed-worker",
        claimed_at=first_boundary,
        lease_seconds=60,
    )
    assert crashed_claim is not None

    recovered = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=first_boundary + timedelta(seconds=61),
    )

    assert recovered.teardowns_confirmed == 1
    assert runtime.deleted == [row["bot_container_id"]]
    assert row["data"]["service_authority"]["teardown_confirmed"] is True


@pytest.mark.asyncio
async def test_observe_only_denial_never_claims_or_applies_hard_stop() -> None:
    authority = RecordingAuthority(
        mode="observe",
        continuation=_decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-observe-only",
            stop_scope="billable_service",
        ),
    )
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]

    observed = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=1, seconds=1),
    )

    persisted = row["data"]["service_authority"]
    assert observed.decisions == 1
    assert observed.teardowns_confirmed == 0
    assert persisted["enforced"] is False
    assert persisted["teardown_confirmed"] is False
    assert row["status"] == "active"
    assert runtime.deleted == []


@pytest.mark.asyncio
async def test_sweep_catches_up_every_boundary_and_restart_skips_none() -> None:
    authority = RecordingAuthority()
    repo, runtime, meeting = await _spawn(authority)
    row = repo._meetings[meeting["id"]]
    row["status"] = "active"
    row["data"]["status_transition"] = [{
        "to": "active",
        "timestamp": ADMITTED_AT.isoformat(),
        "timestamp_source": "producer",
    }]

    first = await run_service_authority_sweep(
        repo,
        runtime,
        authority,
        now=ADMITTED_AT + timedelta(minutes=3, seconds=1),
    )
    restarted_authority = RecordingAuthority()
    second = await run_service_authority_sweep(
        repo,
        runtime,
        restarted_authority,
        now=ADMITTED_AT + timedelta(minutes=4, seconds=1),
    )

    assert first.decisions == 3
    assert second.decisions == 1
    boundaries = [
        request.boundary_at
        for request in authority.requests
        if request.action == "continue"
    ] + [
        request.boundary_at
        for request in restarted_authority.requests
        if request.action == "continue"
    ]
    assert boundaries == [
        ADMITTED_AT + timedelta(minutes=minute)
        for minute in (1, 2, 3, 4)
    ]
    assert runtime.deleted == []


def test_config_is_explicit_and_fail_closed_when_enabled() -> None:
    assert isinstance(
        build_service_authority_from_env({}),
        AllowAllServiceAuthority,
    )

    with pytest.raises(ValueError, match="secret"):
        build_service_authority_from_env({
            "VEXA_SERVICE_AUTHORITY_CONFIG": json.dumps({
                "url": "https://authority.example.test/check",
                "contract_version": "service-authority.v1",
                "timeout_ms": 2_000,
                "response_max_age_seconds": 30,
                "failure_policy": "closed",
                "mode": "enforce",
            }),
        })

    with pytest.raises(ValueError, match="failure_policy"):
        build_service_authority_from_env({
            "VEXA_SERVICE_AUTHORITY_CONFIG": json.dumps({
                "url": "https://authority.example.test/check",
                "contract_version": "service-authority.v1",
                "timeout_ms": 2_000,
                "response_max_age_seconds": 30,
                "failure_policy": "open",
                "mode": "enforce",
            }),
            "VEXA_SERVICE_AUTHORITY_SECRET": "fixture-secret",
        })

    with pytest.raises(ValueError, match="URL"):
        build_service_authority_from_env({
            "VEXA_SERVICE_AUTHORITY_CONFIG": json.dumps({
                "contract_version": "service-authority.v1",
                "timeout_ms": 2_000,
                "response_max_age_seconds": 30,
                "failure_policy": "closed",
                "mode": "enforce",
            }),
            "VEXA_SERVICE_AUTHORITY_SECRET": "fixture-secret",
        })
