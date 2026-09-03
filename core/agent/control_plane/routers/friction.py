"""routers/friction.py — The rough-edges ledger's HTTP door on agent-api (PRD decision 33; #1510).

`friction-sink-in-flows` (255775ab4) moved the friction CARRIER onto flows — `POST /friction`
there, admitted in-process, is the one place the fact is written down, and it is the carrier's
ONLY producing domain (a carrier has exactly one — `core/flows/contracts/flows.v1/carriers.json`'s
own entry says so, and `gate:config-contract` enforces it against every service). This route does
not store anything any more (the old `FrictionStore` — `control_plane/friction.py` — is deleted,
#1510's C5): it is an HTTP CLIENT of flows' existing route (`control_plane.publish.post_friction`),
for the two callers that cannot reach flows directly — the worker's auto-filer (no flows credential
in a worker container) and the terminal's "Report this" (no flows address on the terminal's host).
The rig posts straight to flows now too (`deploy/dogfood/rig/vexa_control_mcp.py`'s
`report_friction`); this route is not its door any more.

`shared/friction.py`'s `normalize()` still does the shape validation, clipping and redaction —
one contract, three producers (worker / terminal / this route) — it just no longer feeds a store,
it feeds `control_plane/publish.py`'s `post_friction()`, which POSTs onto the exact same route
flows itself serves, using the operator key/URL pair `desk.unscaffolded`/`claim.proposed` already
declare.
"""
from __future__ import annotations

from control_plane.api_shared import logger
from control_plane import publish as publish_mod
from fastapi import APIRouter, HTTPException, Request
from shared import friction as friction_mod


def build(**d) -> APIRouter:
    """The friction route, bound to one app's dependencies."""
    router = APIRouter()
    _friction_subject = d['_friction_subject']

    @router.post("/api/friction", status_code=201)
    def file_friction(body: dict, request: Request):
        """File one rough edge — from an agent (`report_friction`), the harness, or a person's
        "Report this" (decision 33 §§1-2). Forwards onto flows' own `POST /friction` and returns
        exactly what it returned — `{id, recorded}` — since this is a forward and not a second
        contract (#1510's C1/C4). When flows cannot be reached (or refuses — no account to
        attribute an anonymous report to), returns `{id: "", recorded: False}` rather than
        inventing a local id that nothing durable backs."""
        # THE SUBJECT IS THE CALLER'S, NEVER THE BODY'S (R-E04) — unchanged from the store era: an
        # unattributable report beats none, but a body that could name a subject let an anonymous
        # caller write records attributed to a named user, which is worse than an unattributed
        # record in exactly the way a forged signature is worse than none.
        raw = dict(body or {})
        rec = friction_mod.normalize({**raw, "subject": _friction_subject(request)})
        if not rec.get("session"):
            raise HTTPException(status_code=400, detail=(
                "session is required — the chat or meeting session this happened in. A report "
                "with no session cannot be tied back to the conversation that produced it, which "
                "is the exact gap the flows carrier exists to close."))
        if not rec.get("tried") or not rec.get("happened"):
            raise HTTPException(status_code=400, detail=(
                "what_i_tried (tried) and what_happened (happened) are both required — "
                "half-formed is fine, empty is not"))
        deployment = str(raw.get("deployment") or "").strip()[:200]
        worker_image = str(raw.get("worker_image") or "").strip()[:200]
        ok, resp = publish_mod.post_friction(rec, deployment=deployment, worker_image=worker_image)
        fid = resp.get("id", "") if ok and isinstance(resp, dict) else ""
        logger.info("friction filed kind=%s published=%s id=%s tool=%s",
                    rec.get("kind"), ok, fid or "-", (rec.get("context") or {}).get("tool") or "-")
        return {"id": fid, "recorded": bool(ok and fid)}

    return router
