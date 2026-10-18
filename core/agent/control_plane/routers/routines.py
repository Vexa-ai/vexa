"""routers/routines.py — Routines: the clock that wakes agents. A routine compiles to a schedule.v1 cron
job whose body is a unit.v1 dispatch, signed by agent-api, that the scheduler POSTs back to
`/invocations` when it is due (`routers/ingress.py`). Create, list, confirm, switch on or off, delete.

Moved out of `routers/chats.py` byte for byte; `build()` rebinds each dependency to the name the
handlers already used.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from jsonschema.exceptions import ValidationError

from control_plane import routines as routines_mod
from control_plane import workspace_routines as workspace_routines_mod
from control_plane.bodies import RoutineCreate, RoutineEnabledPatch


def build(**d) -> APIRouter:
    """The routines routes, bound to one app's dependencies."""
    router = APIRouter()
    dispatcher = d['dispatcher']
    invocations_url = d['invocations_url']
    scheduler = d['scheduler']
    settings = d['settings']
    subject_of = d['subject_of']
    wsr = d['wsr']

    def _internal_secret() -> str:
        return settings.internal_api_secret.get_secret_value() if settings is not None else ""

    @router.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate, request: Request):
        # A routine is a dispatch armed for later; an unwatched worker does not arm one (`person` in
        # routes.v1.json, refused by the app's one gate before this runs).
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        try:
            routine = routines_mod.make_routine(
                subject=subject_of(request), name=body.name, cron=body.cron, prompt=body.prompt,
            )
            job_spec = routines_mod.compile_to_job(routine, invocations_url=invocations_url,
                                                   signing_secret=_internal_secret())
        except (ValueError, ValidationError) as e:  # bad cron form / non-conformant routine — fail loud
            raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))
        job = scheduler.schedule(job_spec)
        ran_now = False
        if body.run_now:
            # Fire one immediate run via the dispatcher (no HTTP hop) so the author sees a result now.
            try:
                dispatcher.dispatch(job_spec["request"]["body"])
                ran_now = True
            except Exception:  # noqa: BLE001 — the routine is still scheduled even if the demo run fails
                ran_now = False
        return {"routine": routine, "job_id": job.get("job_id"), "ran_now": ran_now}
    @router.get("/api/routines")
    def list_routines(request: Request):
        if scheduler is None:
            return {"routines": []}
        cards = workspace_routines_mod.routine_cards_for_subject(
            subject_of(request),
            jobs=scheduler.list_jobs(limit=1000),
            workspaces_dir=wsr.root,
        )
        return {"routines": cards}
    @router.post("/api/routines/{name}/confirm")
    def confirm_routine(name: str, request: Request):
        """A PERSON STANDS BEHIND THIS ROUTINE, exactly as its file reads now — the act that arms a
        routine shown as `pending_confirmation` (`workspace_routines.PENDING`): one written by a
        worker dispatched without a person, or straight onto the workspace by an agent."""
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        try:
            if workspace_routines_mod.approve_routine_file(subject, name, workspaces_dir=wsr.root) is None:
                raise HTTPException(status_code=404, detail="unknown routine")
            result = workspace_routines_mod.reconcile_workspace_routines(
                subject, scheduler=scheduler, invocations_url=invocations_url,
                workspaces_dir=wsr.root, signing_secret=_internal_secret())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "name": name, "confirmed": True, "reconcile": result.__dict__}

    @router.patch("/api/routines/{name}/enabled")
    def set_routine_enabled(name: str, body: RoutineEnabledPatch, request: Request):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        try:
            # The toggle rewrites one line of the file. It carries an approval across that rewrite;
            # it does not grant one — a pending routine is armed by `confirm`, not by a switch.
            path = workspace_routines_mod._safe_routine_path(wsr.root, subject, name)
            approved = path.is_file() and workspace_routines_mod.routine_file_approved(
                path, subject=subject, workspaces_dir=wsr.root)
            workspace_routines_mod.set_routine_file_enabled(
                subject,
                name,
                enabled=body.enabled,
                workspaces_dir=wsr.root,
            )
            if approved:
                workspace_routines_mod.approve_routine_file(subject, name, workspaces_dir=wsr.root)
            result = workspace_routines_mod.reconcile_workspace_routines(
                subject,
                scheduler=scheduler,
                invocations_url=invocations_url,
                workspaces_dir=wsr.root,
                signing_secret=_internal_secret(),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown routine")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "ok": True,
            "name": name,
            "enabled": body.enabled,
            "reconcile": result.__dict__,
        }
    @router.delete("/api/routines/{routine_id}")
    def delete_routine(routine_id: str, request: Request):
        if scheduler is None:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        for job in scheduler.list_jobs():
            meta = job.get("metadata") or {}
            if meta.get("routine_id") == routine_id and meta.get("owner") == subject:
                scheduler.cancel_job(job["job_id"])
                return {"ok": True, "routine_id": routine_id}
        raise HTTPException(status_code=404, detail="unknown routine")

    return router
