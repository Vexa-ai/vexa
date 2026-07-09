"""The WorkloadStore port — where the kernel keeps each workload's (spec, status) so the runtime can
list/query/enforce without re-deriving from the backend, and so state survives a process restart.

Two adapters implement one Protocol:
  • InMemoryStore — the default; a plain dict. Single-process, lost on restart.
  • RedisStore    — serializes (status, spec) as JSON under a key prefix, mirroring 0.11's
                    `runtime_api/state.py` (KEY_PREFIX, scan-based list, count-for-owner). State
                    survives a restart: a fresh Runtime built over the same Redis re-reads it.

`owner` is the tenancy/quota axis. runtime.v1's spec has no owner field (tenancy is deferred,
ADR-0003), so the owner is resolved from the spec by an injectable resolver (default: the
`VEXA_OWNER` env entry) and persisted on the record — `count_for_owner` then enforces quotas (O-RT-2)
without the kernel needing a tenancy concept of its own."""
from __future__ import annotations

import json
from typing import Callable, Optional, Protocol

from .models import WorkloadSpec, WorkloadStatus


def default_owner(spec: WorkloadSpec) -> str:
    """Resolve a workload's owner for quota accounting. runtime.v1 keeps tenancy out of the
    contract, so the owner travels in env (12-factor) under VEXA_OWNER; absent → "" (unowned)."""
    return spec.env.get("VEXA_OWNER", "")


class WorkloadRecord:
    """One persisted workload: its spec, its last-known status, and its resolved owner."""

    __slots__ = ("spec", "status", "owner")

    def __init__(self, spec: WorkloadSpec, status: WorkloadStatus, owner: str) -> None:
        self.spec = spec
        self.status = status
        self.owner = owner

    def to_json(self) -> str:
        return json.dumps(
            {
                "spec": self.spec.model_dump(exclude_none=True),
                "status": self.status.model_dump(exclude_none=True),
                "owner": self.owner,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "WorkloadRecord":
        d = json.loads(raw)
        return cls(
            spec=WorkloadSpec.model_validate(d["spec"]),
            status=WorkloadStatus.model_validate(d["status"]),
            owner=d.get("owner", ""),
        )


class WorkloadStore(Protocol):
    """The persistence port. Synchronous, mirroring the kernel's synchronous operations."""

    def set(self, record: WorkloadRecord) -> None: ...
    def get(self, workload_id: str) -> Optional[WorkloadRecord]: ...
    def list(self) -> list[WorkloadRecord]: ...
    def delete(self, workload_id: str) -> None: ...
    def count_for_owner(self, owner: str) -> int:
        """Count active (non-terminal) workloads belonging to `owner` — the quota axis."""
        ...


# States that no longer occupy a quota slot.
_TERMINAL = {"stopped", "destroyed"}


def _is_active(record: WorkloadRecord) -> bool:
    return record.status.state.value not in _TERMINAL


class InMemoryStore:
    """Default store — a process-local dict. Existing kernel tests run over this unchanged."""

    def __init__(self) -> None:
        self._records: dict[str, WorkloadRecord] = {}

    def set(self, record: WorkloadRecord) -> None:
        self._records[record.spec.workloadId] = record

    def get(self, workload_id: str) -> Optional[WorkloadRecord]:
        return self._records.get(workload_id)

    def list(self) -> list[WorkloadRecord]:
        return list(self._records.values())

    def delete(self, workload_id: str) -> None:
        self._records.pop(workload_id, None)

    def count_for_owner(self, owner: str) -> int:
        return sum(1 for r in self._records.values() if r.owner == owner and _is_active(r))


class RedisStore:
    """Redis-backed store — JSON under `{prefix}{workloadId}`, scan-based list (mirrors 0.11
    state.py). Accepts any redis-py-compatible client (real redis, or fakeredis in evals).

    decode_responses is assumed True (str keys/values); we tolerate bytes defensively so a caller
    that forgot the flag still works."""

    KEY_PREFIX = "runtime:workload:"

    def __init__(self, redis, prefix: str = KEY_PREFIX) -> None:
        self._r = redis
        self._prefix = prefix

    def _key(self, workload_id: str) -> str:
        return f"{self._prefix}{workload_id}"

    @staticmethod
    def _s(v) -> str:
        return v.decode() if isinstance(v, (bytes, bytearray)) else v

    def set(self, record: WorkloadRecord) -> None:
        self._r.set(self._key(record.spec.workloadId), record.to_json())

    def get(self, workload_id: str) -> Optional[WorkloadRecord]:
        raw = self._r.get(self._key(workload_id))
        if raw is None:
            return None
        return WorkloadRecord.from_json(self._s(raw))

    def list(self) -> list[WorkloadRecord]:
        records: list[WorkloadRecord] = []
        for key in self._r.scan_iter(match=f"{self._prefix}*"):
            raw = self._r.get(key)
            if raw is None:
                continue
            records.append(WorkloadRecord.from_json(self._s(raw)))
        return records

    def delete(self, workload_id: str) -> None:
        self._r.delete(self._key(workload_id))

    def count_for_owner(self, owner: str) -> int:
        count = 0
        for key in self._r.scan_iter(match=f"{self._prefix}*"):
            raw = self._r.get(key)
            if raw is None:
                continue
            record = WorkloadRecord.from_json(self._s(raw))
            if record.owner == owner and _is_active(record):
                count += 1
        return count


OwnerResolver = Callable[[WorkloadSpec], str]
