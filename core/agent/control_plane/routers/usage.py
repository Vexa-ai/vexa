"""routers/usage.py — the usage meter: how much of the caller's plan is used (PLAN-8H 6.2, decision G1).

Behind the ``usage`` feature (off by default: both routes answer 403 ``feature_disabled``).

    GET  /api/usage    the caller's 5-hour and weekly windows as a percentage of the plan's allowance,
                       when each resets, and extra usage (``credits.state``)
    POST /api/usage    {"extra": true|false, "topup_credits"?: n} — turning extra usage OFF is
                       anyone's, for themselves. Turning it ON, or recording a top-up, is the
                       instance admin's only: no payment is taken here, so a self-serve switch
                       would be unmetered use for whoever pressed it.

Numbers only: no prompt, model output or other person's usage is ever answered.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import credits


class ExtraBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extra: bool = Field(description="true: carry on past a full window (counted as extra usage); false: stop at it")
    topup_credits: Optional[float] = Field(default=None, ge=0, le=100_000,
                                           description="a top-up amount in credits ($), recorded only — no payment")


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    dispatcher = d.get("dispatcher")

    def _ledger():
        budget = getattr(dispatcher, "_llm_budget", None)
        r = getattr(budget, "_r", None)
        if budget is None or r is None:
            raise HTTPException(status_code=503, detail="no usage ledger on this deployment")
        return budget, r

    @router.get("/api/usage")
    def usage_read(request: Request):
        """How much of your plan is used: the 5-hour and weekly windows as a percentage, when each
        resets, and whether extra usage is on."""
        budget, r = _ledger()
        return credits.state(r, str(subject_of(request)), budget.policy_front())

    @router.post("/api/usage")
    def usage_extra(body: ExtraBody, request: Request):
        """Turn extra usage off for yourself; turning it on (or recording a top-up) is the admin's."""
        budget, r = _ledger()
        payer = str(subject_of(request))
        if (body.extra or body.topup_credits is not None) and not (
                dispatcher is not None and dispatcher.is_admin(payer)):
            raise HTTPException(status_code=403, detail={
                "refused": "extra_usage_not_self_serve",
                "note": "extra usage is turned on with a payment, not from here"})
        credits.set_extra(r, payer, body.extra, topup_credits=body.topup_credits)
        return credits.state(r, payer, budget.policy_front())

    return router
