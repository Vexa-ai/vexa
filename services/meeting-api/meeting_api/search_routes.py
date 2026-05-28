"""Search routes — Phase 3 MVP."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .search_service import search_meetings, ask_about_meetings

router = APIRouter()


@router.get("/internal/search")
async def search_endpoint(
    q: str,
    user_id: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    return await search_meetings(q, user_id, db, limit)


@router.post("/internal/search/ask")
async def ask_endpoint(
    data: dict,
    user_id: int = 1,
    db: AsyncSession = Depends(get_db),
):
    question = data.get("question", "")
    if not question:
        raise HTTPException(status_code=400, detail="Missing 'question' field")
    return await ask_about_meetings(question, user_id, db)
