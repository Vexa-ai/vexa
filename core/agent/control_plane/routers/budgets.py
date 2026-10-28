"""routers/budgets.py — the company's budgets and model policy, as people and admins see them (#1783).

    GET  /api/budgets          the caller's effective limits (the company's, narrowed by their own),
                               today's usage, and every model the policy names marked available or
                               unavailable in this install
    POST /api/budgets/mine     the caller narrows their own limits (never widens: `budgets.narrow`)
    GET  /api/budgets/usage    ADMIN: one line per person — tokens used in the rolling day, percent of
                               the budget, the background share (tool `usage_summary`)

The policy itself is `_global/POLICIES.md`, edited by the admin in Company mode; nothing here writes it.
"""
from __future__ import annotations

from control_plane.workspace_reader import home_of

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import budgets
from shared import usage as usage_mod


class MineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: dict = Field(description="keys from the company policy with a stricter value, e.g. "
                                     "{\"budget_tokens_per_user_day\": 500000}; a wider value is ignored")


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    dispatcher = d.get("dispatcher")
    store = d.get("session_store")
    wsr = d.get("wsr")
    global_root = d.get("_global_root")
    redis_url = d.get("redis_url")

    def _gdir():
        try:
            return global_root() if global_root else None
        except Exception:  # noqa: BLE001
            return None

    def _desk(subject: str):
        try:
            return home_of(wsr, subject) if wsr is not None else None      # the person's home (#1783)
        except Exception:  # noqa: BLE001
            return None

    def _available() -> list:
        cat = getattr(dispatcher, "catalog", None)
        return list(getattr(cat, "ids", []) or []) if cat is not None and not getattr(cat, "empty", True) else []

    @router.get("/api/budgets")
    def mine(request: Request):
        """Your limits today (the company's, narrowed by your own settings), what you have used in the
        last 24 hours, and the models the company allows — each marked available or unavailable."""
        subject = subject_of(request)
        pol = budgets.effective(_gdir(), _desk(subject))
        used = usage_mod.rolling(budgets.usage_client(redis_url), subject)
        return {"limits": pol, "used": used,
                "percent": int(round(100 * used["total"] / max(1, pol["budget_tokens_per_user_day"]))),
                "models": budgets.model_status(pol, _available())}

    @router.post("/api/budgets/mine")
    def narrow_mine(body: MineBody, request: Request):
        """Set your own, stricter limits. A value wider than the company's is ignored, never stored."""
        subject = subject_of(request)
        desk = _desk(subject)
        if desk is None:
            raise HTTPException(status_code=409, detail="no personal workspace yet")
        admin = budgets.read(_gdir())
        kept = {k: v for k, v in body.limits.items() if k in budgets.KEYS
                and budgets.narrow(admin, {k: v}).get(k) != admin.get(k)}
        from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
        wpaths.write_text_inside(desk, ".vexa/limits.json", json.dumps(kept, indent=1))
        return {"kept": kept, "ignored": sorted(set(body.limits) - set(kept))}

    @router.get("/api/budgets/usage")
    def usage_summary(request: Request):
        """ADMIN ONLY: one line per person — tokens their agents used in the last 24 hours, the share
        of the budget, and how much of it was background work (routines, events, the home)."""
        subject = subject_of(request)
        if dispatcher is None or not dispatcher.is_admin(subject):
            raise HTTPException(status_code=403, detail="the usage summary is for the instance admin")
        pol = budgets.read(_gdir())
        cli = budgets.usage_client(redis_url)
        owners: list = []
        if store is not None:
            try:
                with store._lock:
                    owners = [r[0] for r in store._x("SELECT DISTINCT owner FROM agent_session").fetchall()]
            except Exception:  # noqa: BLE001
                owners = []
        lines = []
        for who in owners[:500]:
            u = usage_mod.rolling(cli, who)
            if not u["total"]:
                continue
            pct = int(round(100 * u["total"] / max(1, pol["budget_tokens_per_user_day"])))
            bg = u["routines"] + u["events"] + u["home"]
            lines.append({"subject": who, "tokens": u["total"], "percent": pct, "background": bg,
                          "line": f"{who}: {u['total']:,} tokens ({pct}% of budget; {bg:,} background)"})
        lines.sort(key=lambda r: -r["tokens"])
        return {"budget_tokens_per_user_day": pol["budget_tokens_per_user_day"], "people": lines}

    return router
