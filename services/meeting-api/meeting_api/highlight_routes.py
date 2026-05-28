"""Highlight CRUD routes — Phase 2 MVP."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .highlight_service import (
    get_highlight_by_id,
    create_highlight,
    get_highlights_for_meeting,
    get_highlight_by_token,
    update_highlight,
    delete_highlight,
    generate_clip,
)

router = APIRouter()


@router.post("/internal/meetings/{meeting_id}/highlights")
async def create_highlight_endpoint(
    meeting_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    return await create_highlight(
        db,
        meeting_id=meeting_id,
        start_time=data["start_time"],
        end_time=data["end_time"],
        title=data.get("title"),
        summary=data.get("summary"),
        highlight_type=data.get("type", "custom"),
        speaker=data.get("speaker"),
        source=data.get("source", "manual"),
    )


@router.get("/internal/meetings/{meeting_id}/highlights")
async def list_highlights_endpoint(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await get_highlights_for_meeting(meeting_id, db)


@router.get("/internal/clips/{clip_token}")
async def get_clip_endpoint(
    clip_token: str,
    db: AsyncSession = Depends(get_db),
):
    h = await get_highlight_by_token(clip_token, db)
    if not h:
        raise HTTPException(status_code=404, detail="Clip not found")
    return h


@router.put("/internal/highlights/{highlight_id}")
async def update_highlight_endpoint(
    highlight_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    h = await update_highlight(db, highlight_id, data)
    if not h:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return h


@router.delete("/internal/highlights/{highlight_id}")
async def delete_highlight_endpoint(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_highlight(db, highlight_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return {"deleted": True}


@router.post("/internal/highlights/{highlight_id}/generate-clip")
async def generate_clip_endpoint(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
):
    h = await get_highlight_by_id(highlight_id, db)
    if not h:
        raise HTTPException(status_code=404, detail="Highlight not found")
    token = await generate_clip(h, db)
    return {"clip_token": token}
