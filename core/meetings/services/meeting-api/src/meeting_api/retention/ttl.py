"""Clock-controlled, carrier-independent Minutes retention policy core.

This module chooses no product default and owns no scheduler or database query. The caller supplies
already-materialized UTC expiries plus a bounded store. Production composition is a later slice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol


RetentionScope = Literal["audio", "transcript", "summary"]
RETENTION_SCOPES: tuple[RetentionScope, ...] = ("audio", "transcript", "summary")
MAX_TTL_BATCH = 500


class TtlBatchFailed(RuntimeError):
    """The store returned an invalid due-scope batch; no scope was expired."""


@dataclass(frozen=True)
class ScopeExpiries:
    """Immutable expiry instants materialized from an already-effective capture policy."""

    audio: datetime
    transcript: datetime
    summary: datetime


@dataclass(frozen=True)
class DueScope:
    """One opaque carrier selected for expiry; identities never enter the public receipt."""

    user_id: str
    meeting_id: str
    scope: RetentionScope
    expires_at: datetime


@dataclass(frozen=True)
class TtlBatchReceipt:
    attempted: int
    audio_expired: int
    transcript_expired: int
    summary_expired: int
    failed: int

    @property
    def expired(self) -> dict[RetentionScope, int]:
        """Return a fresh convenience view so callers cannot mutate the receipt."""

        return {
            "audio": self.audio_expired,
            "transcript": self.transcript_expired,
            "summary": self.summary_expired,
        }


class TtlStore(Protocol):
    async def list_due_scopes(
        self, *, now: datetime, limit: int
    ) -> tuple[DueScope, ...]:
        """Return at most ``limit`` deterministic carrier candidates due at or before ``now``."""

    async def expire_scope(self, item: DueScope) -> int:
        """Idempotently expire one scope and return its content-free deleted-unit count."""


def _require_utc(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("retention expiry must be an aware UTC instant")


def _validate_expiries(expiries: ScopeExpiries) -> None:
    if not isinstance(expiries, ScopeExpiries):
        raise ValueError("retention expiries are invalid")
    for scope in RETENTION_SCOPES:
        _require_utc(getattr(expiries, scope))


def materialize_scope_expiries(
    proposed: ScopeExpiries, *, existing: ScopeExpiries | None = None
) -> ScopeExpiries:
    """Validate explicit scope expiries and refuse to extend already-stored content.

    Product/config layers calculate ``proposed``. This core only freezes it: an existing expiry can
    move earlier but never later, so a config change cannot silently resurrect or extend content.
    """

    _validate_expiries(proposed)
    if existing is None:
        return proposed
    _validate_expiries(existing)
    return ScopeExpiries(
        **{
            scope: min(getattr(proposed, scope), getattr(existing, scope))
            for scope in RETENTION_SCOPES
        }
    )


def _validate_due_batch(
    items: tuple[DueScope, ...], *, now: datetime, limit: int
) -> None:
    if not isinstance(items, tuple) or len(items) > limit:
        raise TtlBatchFailed("TTL store returned an invalid batch")
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if (
            not isinstance(item, DueScope)
            or item.scope not in RETENTION_SCOPES
            or not isinstance(item.user_id, str)
            or not item.user_id
            or not isinstance(item.meeting_id, str)
            or not item.meeting_id
        ):
            raise TtlBatchFailed("TTL store returned an invalid candidate")
        try:
            _require_utc(item.expires_at)
        except ValueError:
            raise TtlBatchFailed("TTL store returned an invalid candidate") from None
        identity = (item.user_id, item.meeting_id, item.scope)
        if item.expires_at > now or identity in seen:
            raise TtlBatchFailed("TTL store returned an invalid candidate")
        seen.add(identity)


async def run_ttl_batch(
    store: TtlStore,
    *,
    now: datetime,
    limit: int = 100,
) -> TtlBatchReceipt:
    """Expire one bounded due-scope batch and return no content or carrier identity.

    Candidate validation happens before the first mutation. Individual adapter failures are counted
    and the batch continues; the adapter must leave a failed scope due so the next run retries it.
    """

    _require_utc(now)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TTL_BATCH:
        raise ValueError(f"TTL batch limit must be between 1 and {MAX_TTL_BATCH}")
    try:
        items = await store.list_due_scopes(now=now, limit=limit)
    except Exception:
        raise TtlBatchFailed("TTL store selection requires retry") from None
    _validate_due_batch(items, now=now, limit=limit)

    expired: dict[RetentionScope, int] = {scope: 0 for scope in RETENTION_SCOPES}
    failed = 0
    for item in items:
        try:
            deleted = await store.expire_scope(item)
            if isinstance(deleted, bool) or not isinstance(deleted, int) or deleted < 0:
                raise RuntimeError("invalid TTL adapter receipt")
            expired[item.scope] += deleted
        except Exception:
            failed += 1
    return TtlBatchReceipt(
        attempted=len(items),
        audio_expired=expired["audio"],
        transcript_expired=expired["transcript"],
        summary_expired=expired["summary"],
        failed=failed,
    )
