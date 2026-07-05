"""Diarization routes — speaker identification."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .diarization_service import run_diarization

router = APIRouter()


@router.post("/internal/meetings/{meeting_id}/diarize")
async def diarize_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Run speaker diarization on a meeting's transcript segments."""
    return await run_diarization(meeting_id, db)
