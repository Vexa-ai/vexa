"""Durable one-minute continuation decisions and teardown application."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from ..bot_spawn.ports import WorkloadUnknown
from .models import (
    AUTHORITY_VERSION,
    ServiceAuthorityDecision,
    ServiceAuthorityRequest,
    parse_time,
)
from .ports import ServiceAuthority, ServiceAuthorityUnavailable


@dataclass(frozen=True)
class ServiceAuthoritySweepObservation:
    decisions: int = 0
    teardowns_confirmed: int = 0
    faults: int = 0


def _admitted_at(row: dict[str, Any]) -> Optional[datetime]:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    transitions = data.get("status_transition")
    if not isinstance(transitions, list):
        return None
    for item in reversed(transitions):
        if (
            isinstance(item, dict)
            and item.get("to") == "active"
            and item.get("timestamp_source") == "producer"
        ):
            try:
                return parse_time(item.get("timestamp"), "bot admission")
            except ValueError:
                return None
    return None


def _unavailable_decision(
    request: ServiceAuthorityRequest,
    now: datetime,
) -> ServiceAuthorityDecision:
    digest = hashlib.sha256(request.to_json_bytes()).hexdigest()
    return ServiceAuthorityDecision(
        authority_version=AUTHORITY_VERSION,
        decision_id=f"service-authority:unavailable:{digest}",
        request_id=request.request_id,
        service_identity=request.service_identity,
        allow=False,
        reason="service_authority_unavailable",
        decided_at=now,
        stop_scope="billable_service",
    )


async def run_service_authority_sweep(
    repo,
    runtime,
    authority: ServiceAuthority,
    *,
    now: Optional[datetime] = None,
) -> ServiceAuthoritySweepObservation:
    """Process every due minute in order, then converge pending stops.

    The repository persists a response before the external teardown. A crash
    before the teardown therefore leaves a visible pending intent that the next
    sweep resumes; a confirmed teardown is never invoked again.
    """
    if not authority.configured:
        return ServiceAuthoritySweepObservation()
    observed_at = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    decisions = 0
    teardowns = 0
    faults = 0

    sessions = await repo.list_service_authority_sessions()
    for row in sessions:
        metadata = (
            row.get("data", {}).get("service_authority")
            if isinstance(row.get("data"), dict)
            else None
        )
        if not isinstance(metadata, dict):
            continue
        # The admission mode is frozen with the delivered service.  Unconfigured
        # OSS sessions never become externally controlled merely because a
        # later deployment enables an authority, and an observe→enforce rollout
        # cannot retroactively turn an observed session into a hard-cap claim.
        if metadata.get("mode") != authority.mode:
            faults += 1
            continue
        admitted_at = _admitted_at(row)
        if admitted_at is None:
            faults += 1
            continue
        last_raw = metadata.get("last_boundary_at")
        try:
            last_boundary = (
                parse_time(last_raw, "last service boundary")
                if last_raw
                else admitted_at
            )
        except ValueError:
            faults += 1
            continue
        boundary = last_boundary + timedelta(minutes=1)
        while boundary <= observed_at:
            service_identity = metadata.get("service_identity")
            provider = metadata.get("transcription_provider")
            if (
                not isinstance(service_identity, str)
                or provider not in ("vexa", "customer", "none")
            ):
                faults += 1
                break
            request_id = (
                f"{service_identity}:continue:"
                f"{boundary.isoformat()}"
            )
            active = await repo.count_active_bots(
                user_id=row["user_id"],
            )
            request = ServiceAuthorityRequest.continuation(
                user_id=row["user_id"],
                request_id=request_id,
                service_identity=service_identity,
                transcription_provider=provider,
                active_concurrency=active,
                admitted_at=admitted_at,
                boundary_at=boundary,
            )
            try:
                decision = await authority.decide(request)
            except ServiceAuthorityUnavailable:
                decision = _unavailable_decision(request, observed_at)
                faults += 1
            recorded = await repo.record_service_authority_decision(
                meeting_id=row["id"],
                request=request,
                decision=decision,
            )
            if recorded:
                decisions += 1
            if decision.enforced and not decision.allow:
                break
            boundary += timedelta(minutes=1)

    for pending in await repo.list_service_authority_teardowns():
        claim = await repo.claim_service_authority_teardown(
            meeting_id=pending["id"],
            claim_id=f"service-authority-teardown:{uuid4()}",
            claimed_at=observed_at,
            lease_seconds=60,
        )
        if claim is None:
            continue
        workload_id = claim.get("bot_container_id")
        if not workload_id:
            faults += 1
            continue
        try:
            await runtime.delete_workload(workload_id)
        except WorkloadUnknown:
            # Runtime 404 means UNTRACKED, not destroyed: a recreated runtime
            # can forget a still-live external workload.  Keep the durable stop
            # pending so re-adoption or another positive teardown can confirm
            # it after the claim lease expires.
            faults += 1
            continue
        except Exception:
            faults += 1
            continue
        confirmed = await repo.confirm_service_authority_teardown(
            meeting_id=claim["id"],
            decision_id=claim["decision_id"],
            claim_id=claim["claim_id"],
        )
        if confirmed:
            teardowns += 1

    return ServiceAuthoritySweepObservation(
        decisions=decisions,
        teardowns_confirmed=teardowns,
        faults=faults,
    )
