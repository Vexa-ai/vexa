"""Raw meeting erasure orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Awaitable, Callable

from .ports import ErasurePlan, RetentionRepo, RetentionStorage


class ErasureFailed(RuntimeError):
    """The operation did not complete; the message never contains meeting content or storage keys."""


_PREFIX_SEGMENT = re.compile(r"^[A-Za-z0-9._:-]+$")


def _valid_recording_prefix(prefix: str) -> bool:
    if not isinstance(prefix, str) or not prefix.endswith("/"):
        return False
    parts = prefix.split("/")
    return (
        len(parts) >= 5
        and parts[0] == "recordings"
        and parts[-1] == ""
        and all(_PREFIX_SEGMENT.fullmatch(part) and part not in {".", ".."} for part in parts[1:-1])
    )


@dataclass(frozen=True)
class ErasureReceipt:
    user_id: str
    meeting_id: str
    erased_at: datetime
    policy_version: str
    meeting_rows: int
    transcript_rows: int
    summary_documents: int
    recording_objects: int

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "meeting_id": self.meeting_id,
            "erased_at": self.erased_at.isoformat(),
            "policy_version": self.policy_version,
            "deleted": {
                "meeting_rows": self.meeting_rows,
                "transcript_rows": self.transcript_rows,
                "summary_documents": self.summary_documents,
                "recording_objects": self.recording_objects,
            },
        }


async def erase_meeting(
    repo: RetentionRepo,
    storage: RetentionStorage,
    *,
    user_id: str,
    meeting_id: str,
    erased_at: datetime,
    policy_version: str,
    before_delete: Callable[[ErasurePlan], Awaitable[None]] | None = None,
) -> ErasureReceipt | None:
    """Erase one owned meeting without exposing whether an absent meeting belongs to another user."""

    try:
        plan = await repo.begin_erasure(user_id, meeting_id)
    except Exception:
        raise ErasureFailed("meeting erasure planning requires retry") from None
    if plan is None:
        return None
    prefixes = plan.recording_prefixes
    if any(not _valid_recording_prefix(prefix) for prefix in prefixes):
        raise ErasureFailed("meeting erasure plan is invalid")

    if plan.recording_objects is None:
        try:
            recording_objects = sum(
                [await storage.count_prefix(prefix) for prefix in prefixes]
            )
            plan = await repo.record_object_census(plan, recording_objects)
        except Exception:
            raise ErasureFailed("meeting erasure census requires retry") from None

    if (
        plan.recording_objects is None
        or plan.recording_objects < 0
        or (not prefixes and plan.recording_objects != 0)
    ):
        raise ErasureFailed("meeting erasure plan is invalid")

    # Persist an operation-owned receipt plan before the first irreversible object deletion.
    # A process crash after this hook can return the same counts on retry even if the raw meeting
    # row is already gone, without retaining any content identifiers in the receipt ledger.
    if before_delete is not None:
        try:
            await before_delete(plan)
        except Exception:
            raise ErasureFailed("meeting erasure receipt preparation requires retry") from None

    try:
        for prefix in plan.recording_prefixes:
            await storage.delete_prefix(prefix)
        if any([await storage.count_prefix(prefix) for prefix in plan.recording_prefixes]):
            raise RuntimeError("recording prefix remains non-empty")
    except Exception:
        raise ErasureFailed("meeting erasure failed before database commit") from None

    try:
        deleted = await repo.commit_erasure(plan)
    except Exception:
        raise ErasureFailed("meeting erasure requires retry") from None

    return ErasureReceipt(
        user_id=plan.user_id,
        meeting_id=plan.meeting_id,
        erased_at=erased_at,
        policy_version=policy_version,
        meeting_rows=deleted.get("meeting_rows", 0),
        transcript_rows=deleted.get("transcript_rows", 0),
        summary_documents=deleted.get("summary_documents", 0),
        recording_objects=plan.recording_objects,
    )
