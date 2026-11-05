"""The shadow's reading, for the internal tier only. Mounted only in shadow mode."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request


def build_reading_router(observer, *, internal_secret: str) -> APIRouter:
    router = APIRouter()

    @router.get("/internal/bot-admission/reading", include_in_schema=False)
    async def reading(request: Request) -> dict:
        presented = request.headers.get("x-internal-secret") or ""
        if not (internal_secret and presented
                and hmac.compare_digest(presented.encode(), internal_secret.encode())):
            raise HTTPException(status_code=403, detail="internal only")
        try:
            return await observer.reading()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="reading unavailable") from exc

    return router
