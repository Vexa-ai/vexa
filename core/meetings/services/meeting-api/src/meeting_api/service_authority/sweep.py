"""Durable one-minute continuation decisions and teardown application."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from ..bot_spawn.ports import WorkloadUnknown
from .models import ServiceAuthorityRequest, parse_time
from .ports import ServiceAuthority, ServiceAuthorityUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceAuthoritySweepObservation:
    decisions: int = 0
    teardowns_confirmed: int = 0
    faults: int = 0
    # Debt minutes (fail-open boundaries) that reached a real decision this
    # tick.  The durable totals live on the row:
    # ``data.service_authority.debt_open`` / ``debt_settled_count``.
    debt_settled_count: int = 0
    # Whether any swept session still carries an unsettled debt minute.
    debt_open: bool = False


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


def _debt_by_boundary(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = metadata.get("debt_boundaries")
    if not isinstance(entries, list):
        return {}
    return {
        item["boundary"]: item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("boundary"), str)
    }


def _stop_pending(metadata: dict[str, Any]) -> bool:
    """The row's last decision is an enforced stop not yet torn down."""
    return (
        metadata.get("enforced") is True
        and metadata.get("allow") is False
        and metadata.get("stop_scope") == "billable_service"
        and metadata.get("teardown_confirmed") is not True
    )


def _pending_minutes(
    debt: dict[str, dict[str, Any]],
    last_boundary: datetime,
    until: datetime,
    *,
    live: bool,
) -> list[datetime]:
    """Minutes to ask about this tick, in order.

    A live row owes every minute after ``last_boundary`` up to ``until`` (its
    debt minutes are among them, since booking debt never moves the boundary).
    A row that is no longer live owes only the debt minutes it already booked.
    """
    if not live:
        return sorted(parse_time(key, "debt boundary") for key in debt)
    pending = []
    boundary = last_boundary + timedelta(minutes=1)
    while boundary <= until:
        pending.append(boundary)
        boundary += timedelta(minutes=1)
    return pending


async def _continuation_request(
    repo: Any,
    *,
    row: dict[str, Any],
    service_identity: str,
    provider: Any,
    admitted_at: datetime,
    boundary: datetime,
    debt_entry: Optional[dict[str, Any]],
) -> ServiceAuthorityRequest:
    """The continuation request for one minute; same ``request_id`` on replay."""
    stored = debt_entry.get("active_concurrency") if debt_entry else None
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0:
        # Replay a debt minute with the concurrency observed when it was
        # served, not with today's count.
        active = stored
    else:
        active = await repo.count_active_bots(user_id=row["user_id"])
    return ServiceAuthorityRequest.continuation(
        user_id=row["user_id"],
        request_id=f"{service_identity}:continue:{boundary.isoformat()}",
        service_identity=service_identity,
        transcription_provider=provider,
        active_concurrency=active,
        admitted_at=admitted_at,
        boundary_at=boundary,
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

    An admitted session FAILS OPEN when the authority is unavailable: the
    minute is booked as debt (``record_service_authority_debt``) and no
    decision is recorded, so ``last_boundary_at`` stays put and the same
    boundary — same ``request_id`` — is asked again on every later tick until
    the authority answers.  The bot keeps running.  Once one call fails in a
    tick, no further authority call is made that tick (the outage costs one
    timeout per sweep, not one per due minute per live meeting).  New
    admissions keep failing closed in the router (503).

    Debt outlives the session: a row stopped by a catch-up deny, or ended
    during the outage, is still listed (``list_service_authority_debt_sessions``)
    and its remaining debt minutes are replayed from the next tick, because the
    bot did run through them.  Such a row books no new minute, and a deny on
    one of its minutes settles the minute without a stop.
    """
    if not authority.configured:
        return ServiceAuthoritySweepObservation()
    observed_at = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    decisions = 0
    teardowns = 0
    faults = 0
    debt_settled = 0
    debt_open = False
    # Per-tick circuit: set on the first ServiceAuthorityUnavailable.
    outage = False

    sessions = await repo.list_service_authority_sessions()
    live_ids = {row.get("id") for row in sessions}
    # Rows no longer live whose debt is unsettled: replayed, never extended.
    indebted = [
        row
        for row in await repo.list_service_authority_debt_sessions()
        if row.get("id") not in live_ids
    ]
    for row, live in [(r, True) for r in sessions] + [(r, False) for r in indebted]:
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
        if not live and _stop_pending(metadata):
            # The persisted stop outranks the replay: a later allow would
            # overwrite the intent that list_service_authority_teardowns reads.
            # The debt stays open and is replayed once the teardown confirms.
            debt_open = True
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
        debt = _debt_by_boundary(metadata)
        open_debt = set(debt)
        try:
            pending = _pending_minutes(
                debt, last_boundary, observed_at, live=live
            )
        except ValueError:
            faults += 1
            debt_open = True
            continue
        for boundary in pending:
            service_identity = metadata.get("service_identity")
            provider = metadata.get("transcription_provider")
            if (
                not isinstance(service_identity, str)
                or provider not in ("vexa", "customer", "none")
            ):
                faults += 1
                break
            boundary_key = boundary.isoformat()
            request = await _continuation_request(
                repo,
                row=row,
                service_identity=service_identity,
                provider=provider,
                admitted_at=admitted_at,
                boundary=boundary,
                debt_entry=debt.get(boundary_key),
            )
            decision = None
            if not outage:
                try:
                    decision = await authority.decide(request)
                except ServiceAuthorityUnavailable:
                    # One fault per tick for the outage, not one per minute.
                    outage = True
                    faults += 1
            if decision is None:
                if not live:
                    # Its minutes are already booked; nothing new this tick.
                    break
                booked = await repo.record_service_authority_debt(
                    meeting_id=row["id"],
                    request=request,
                )
                if booked:
                    logger.error(
                        "service_authority_fail_open meeting_id=%s "
                        "boundary=%s reason=unavailable",
                        row["id"],
                        boundary_key,
                    )
                open_debt.add(boundary_key)
                continue
            recorded = await repo.record_service_authority_decision(
                meeting_id=row["id"],
                request=request,
                decision=decision,
            )
            if recorded:
                decisions += 1
                if boundary_key in open_debt:
                    open_debt.discard(boundary_key)
                    debt_settled += 1
            if decision.enforced and not decision.allow and live:
                # The stop is persisted; the row is not live from here, so its
                # later debt minutes are replayed on the next tick (B6).
                break
        if open_debt:
            debt_open = True

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
        debt_settled_count=debt_settled,
        debt_open=debt_open,
    )
