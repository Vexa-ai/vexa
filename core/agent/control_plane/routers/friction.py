"""routers/friction.py — The rough-edges ledger (PRD decision 33). SEPARATE ON PURPOSE: PRD 40.7 makes the agent
domain optional, and where friction belongs is the founder's open question right now —
kept whole and self-contained so it can move domains as one file, not as a grep.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import friction as friction_store_mod
from control_plane import global_layer
from control_plane.api_shared import logger
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from shared import friction as friction_mod


def build(**d) -> APIRouter:
    """The friction routes, bound to one app's dependencies."""
    router = APIRouter()
    _friction_subject = d['_friction_subject']
    friction = d['friction']
    settings = d['settings']
    subject_of = d['subject_of']

    @router.post("/api/friction", status_code=201)
    def file_friction(body: dict, request: Request):
        """File one rough edge — from an agent (`report_friction`), the harness, or a person's
        "Report this" (decision 33 §§1–2). Returns the stored record; `recurrence > 1` means this
        edge was already known and this report is another occurrence of it."""
        # THE SUBJECT IS THE CALLER'S, NEVER THE BODY'S (R-E04). The route is deliberately
        # unauthenticated — an unattributable report beats none — but a body that could name a
        # subject let an anonymous caller write records attributed to a named user, which is worse
        # than an unattributed record in exactly the way a forged signature is worse than none.
        rec = friction.file({**(body or {}), "subject": _friction_subject(request)})
        logger.info("friction filed id=%s kind=%s status=%s recurrence=%s tool=%s",
                    rec.get("id"), rec.get("kind"), rec.get("status"), rec.get("recurrence"),
                    (rec.get("context") or {}).get("tool") or "-")
        return {"id": rec["id"], "status": rec["status"], "recurrence": rec["recurrence"],
                "kind": rec["kind"], "known": rec["recurrence"] > 1}
    @router.get("/api/friction/dump")
    def dump_friction(request: Request, since: str = "", status: str = "open",
                      format: str = "md"):
        """THE DUMP (decision 33 §3) — the open rough edges as a brief a fixing agent works off.

        `format=md` (default) returns the markdown; `format=json` returns the same grouping as data,
        which is what the rig's `friction_so_far` renders. Both come from ONE renderer in
        `shared/friction.py`: a dump and a tool that disagree about what is open is the two-renderers
        failure this codebase has already paid for twice."""
        subject = subject_of(request)   # a read: identified callers only, like every other read here
        ts = friction_store_mod.parse_since(since)
        rows = friction.since(ts, status=status)
        # A report carries the workspace names, paths and free text of what a person was DOING when
        # it broke. So the dump is the caller's own, with one exception: the fixing agent of
        # decision 33 §3 is the instance admin, and it is the whole point of the dump (R-E05).
        if not global_layer.is_admin(settings, str(subject)):
            rows = [r for r in rows if str(r.get("subject") or "") == str(subject)]
        if format == "json":
            return {"since": since, "status": status, "count": len(rows),
                    "findings": friction_mod.group(rows), "records": rows}
        return Response(content=friction_mod.render_markdown(rows, since=since, status=status),
                        media_type="text/markdown; charset=utf-8")
    @router.post("/api/friction/{friction_id}/fix")
    def fix_friction(friction_id: str, body: dict, request: Request):
        """Close one record against the change that addressed it (decision 33 §4).

        A record filed again after this flips itself to `recurring` — which is why closing is cheap
        and why a fixing agent should close what it addressed rather than waiting to be sure."""
        subject = subject_of(request)
        # Same scoping as the dump, and a 404 rather than a 403 for someone else's record: the id is
        # the only thing a prober would learn from the difference (R-E05).
        existing = friction.get(friction_id)
        if existing is None or not (global_layer.is_admin(settings, str(subject))
                                    or str(existing.get("subject") or "") == str(subject)):
            raise HTTPException(status_code=404, detail="no such friction record")
        try:
            rec = friction.fix(friction_id, str((body or {}).get("fix_ref") or ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if rec is None:
            raise HTTPException(status_code=404, detail="no such friction record")
        return {"id": rec["id"], "status": rec["status"], "fix_ref": rec["fix_ref"]}

    return router
