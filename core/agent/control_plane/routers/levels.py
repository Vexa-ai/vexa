"""routers/levels.py — model levels 1–5 for hosted plans (router v0, PLAN-8H 6.3, decision G1).

Behind the ``levels`` feature (off by default; self-host and app.dev keep the full model picker).

    GET  /api/models/levels[?session=]   the five levels — word and the catalog id each runs on, inside
                                         the company's policy — and, asked for a chat, that chat's level
    POST /api/chat/level                 {session, level, switch?} — the chat runs on that level's model.
                                         A chat that already has a level keeps it (prompt caching):
                                         changing it needs ``switch: true`` (409 ``level_fixed`` without)

The pick is stored by the one writer of a chat's model (``SessionIndex.set_model``), after the same
access check the model picker makes, so a level can never run a model the person may not use.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from control_plane import budgets, llm_budget, model_providers, model_router
from shared import units


class LevelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: Optional[str] = Field(default=None, max_length=128, description="the chat; the default chat when absent")
    level: int = Field(ge=1, le=5, description="1 fastest … 5 most capable")
    switch: bool = Field(default=False, description="change a chat that already has a level (resets its cache)")


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    dispatcher = d.get("dispatcher")
    sess = d.get("sess")
    global_root = d.get("_global_root")

    def _table() -> dict:
        catalog = getattr(dispatcher, "catalog", None)
        try:
            pol = budgets.read(global_root() if global_root else None)
        except Exception:  # noqa: BLE001 — an unreadable policy is the defaults
            pol = budgets.defaults()
        budget = getattr(dispatcher, "_llm_budget", None)
        return model_router.levels(catalog, pol, getattr(budget, "prices", None) or llm_budget.default_prices())

    def _chat(subject: str, session: str, table: dict) -> Optional[dict]:
        try:
            mid = str(sess.model(subject, session) or "").strip()
            lv = sess.level(subject, session) if mid else None
        except Exception:  # noqa: BLE001
            return None
        return {"level": lv, "model": mid} if lv else None

    @router.get("/api/models/levels")
    def levels_read(request: Request, session: Optional[str] = None):
        """The five model levels this deployment offers (word and model), and a chat's level."""
        table = _table()
        out: dict = {"levels": [{"level": lv, "word": model_router.WORDS[lv], "model": table.get(lv)}
                                for lv in model_router.LEVELS]}
        if session:
            out["chat"] = _chat(str(subject_of(request)), session, table)
        return out

    @router.post("/api/chat/level")
    def chat_level(body: LevelBody, request: Request):
        """Run this chat on a level. Chosen once: changing a chat's level needs `switch: true`."""
        subject = str(subject_of(request))
        session = str(body.session or "").strip() or units.DEFAULT_CHAT_SESSION
        table = _table()
        mid = table.get(body.level)
        if not mid:
            raise HTTPException(status_code=409, detail="this deployment offers no model for that level")
        now = _chat(subject, session, table)
        if now and now["level"] != body.level and not body.switch:
            raise HTTPException(status_code=409, detail={
                "reason": "level_fixed", "level": now["level"],
                "detail": f"This chat stays at level {now['level']} so its context stays cached. "
                          f"Start a new chat at level {body.level}, or switch (the cache resets)."})
        from control_plane import dispatch as dispatch_mod  # noqa: PLC0415
        ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {})
        try:
            dispatcher.catalog.choose_with_effort(mid, "", ctx, admin=lambda: dispatcher.is_admin(subject),
                                                  person=lambda: dispatcher.identities(subject))
        except model_providers.ModelChoiceFault as fault:
            return JSONResponse(status_code=fault.http_status,
                                content={"detail": fault.sentence(), "fault": fault.as_dict()})
        changed = sess.set_model(subject, session, mid, "", level=body.level)
        return {"ok": True, "session": session, "level": body.level, "word": model_router.WORDS[body.level],
                "model": mid, "changed": changed}

    return router
