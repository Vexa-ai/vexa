"""Raw meeting erasure orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .ports import RetentionRepo, RetentionStorage


class ErasureFailed(RuntimeError):
    """The operation did not complete; the message never contains meeting content or storage keys."""


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
) -> ErasureReceipt | None:
    """Erase one owned meeting without exposing whether an absent meeting belongs to another user."""

    plan = await repo.plan_erasure(user_id, meeting_id)
    if plan is None:
        return None

    recording_objects = 0
    try:
        for key in plan.recording_keys:
            await storage.delete(key)
            recording_objects += 1
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
        recording_objects=recording_objects,
    )
