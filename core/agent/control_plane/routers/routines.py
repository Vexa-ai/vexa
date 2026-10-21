"""routers/routines.py — the DEPRECATED `/api/routines` adapter onto flows (ADR-0046).

A routine is a person's own flow — a schedule and the `agent_run` step, owned and fired by flows,
run as a turn in the routine's own chat thread. These routes are kept for ONE release, for installs
upgrading from 0.13 whose clients still call them, and are removed in `routine_flows.ADAPTER_ENDS`
(0.14.0). Every answer carries `Deprecation: true` and a `Sunset` naming that release. They hold no
routine mechanism of their own: each is one flows-api call for the caller's own scope. A deployment
without flows has no routines, and every route answers `501 routines_need_flows`.

The Routines board reads this list: the person's schedule flows (with why a paused one is paused)
and a card for each old routine file the one-time migration could not turn into a flow, with the
exact reason. There is no confirmation step: a routine an agent wrote arrives as a paused flow.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from control_plane import identity_token
from control_plane.ceiling import refuse_delegated
from control_plane import routine_flows
from control_plane.api_shared import logger
from control_plane import routine_needs
from control_plane.bodies import RoutineCreate, RoutineEnabledPatch


def build(**d) -> APIRouter:
    """The routines adapter routes, bound to one app's dependencies."""
    router = APIRouter()
    subject_of = d['subject_of']
    wsr = d['wsr']

    def _adapter(response: Response) -> None:
        if not routine_flows.configured():
            raise HTTPException(status_code=501, detail={
                "refused": "routines_need_flows",
                "note": "routines are flows; this deployment has no flows-api configured"})
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = f"release {routine_flows.ADAPTER_ENDS}"
        response.headers["Link"] = '</flows>; rel="successor-version"'

    def _background_gate() -> dict:
        """What a routine may reach with nobody in the loop — the policy key and its allowed actions —
        for the flow graph's gate node. Empty when the policy cannot be read."""
        from control_plane import connection_policy  # noqa: PLC0415
        try:
            eff = connection_policy.read(d['_global_root']())
        except Exception:  # noqa: BLE001 — the graph draws without its gate rather than failing
            return {}
        allowed = sorted(eff.get("connection_actions_unattended") or [])
        return {"policy": {"key": "connection_actions_unattended", "allowed": allowed,
                           "refused": [a for a in connection_policy.ACTIONS if a not in allowed]}}

    def _flows_refusal(e: "routine_flows.FlowsError"):
        detail = e.detail if isinstance(e.detail, dict) else {"detail": e.detail}
        return HTTPException(status_code=e.status if 400 <= e.status < 500 else 502, detail=detail)

    def _flow_routine(subject: str, routine_id: str):
        name = routine_id[len(routine_flows.FLOW_ID_PREFIX):] if routine_id.startswith(
            routine_flows.FLOW_ID_PREFIX) else routine_id
        try:
            for f in routine_flows.FlowsClient().routines(subject):
                if f["name"] == name:
                    return f
        except routine_flows.FlowsError as e:
            raise _flows_refusal(e)
        return None

    def _inherit_scope(request: Request, subject: str, made: dict) -> None:
        """A ROUTINE MADE IN A CHAT RUNS WITH THAT CHAT'S SCOPE (Vexa-ai/vexa#1783, founder
        2026-10-15): where it writes, what it reads, or "no workspaces" — copied onto the routine's
        own thread, so a run never falls back to Personal and every private workspace."""
        sess = d.get("sess")
        unit = identity_token.session_of(request.headers)
        if sess is None or not unit:
            return
        try:
            from control_plane.routers.chat_read import _unit_session  # noqa: PLC0415
            chat = _unit_session(unit, subject)
            thread = routine_flows.thread_session(made.get("scope") or f"user:{subject}", made["name"])
            if not chat or thread == chat:
                return
            if sess.no_target(subject, chat):
                sess.set_no_target(subject, thread)
            else:
                sess.set_target(subject, thread, sess.target(subject, chat))
            sess.set_reads(subject, thread, sess.reads(subject, chat))
        except Exception:  # noqa: BLE001 — the routine is saved; its scope is the extra
            logger.warning("routine did not inherit its chat's scope subject=%s", subject)

    @router.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate, request: Request, response: Response):
        # A routine is a dispatch armed for later; an unwatched worker does not arm one (`person` in
        # routes.v1.json, refused by the app's one gate before this runs).
        _adapter(response)
        subject = subject_of(request)
        # AN AGENT'S ROUTINE IS A PROPOSAL (D24): a worker acting for the person files it paused,
        # and the person turns it on from their Routines board. So is one an API key files (D13):
        # only a signed-in session arms a routine as it creates it.
        from control_plane import policy_decide  # noqa: PLC0415
        proposed = policy_decide.credential_of(request.headers) != policy_decide.SESSION
        try:
            made = routine_flows.FlowsClient().submit(subject, body.name, body.cron, body.prompt,
                                                      activate=not proposed)
        except routine_flows.FlowsError as e:
            raise _flows_refusal(e)
        _inherit_scope(request, str(subject), made)
        card = routine_flows.card_from_flow(subject, {
            "name": made["name"], "version": made["version"], "scope": made["scope"],
            "trigger": made.get("trigger"), "status": made.get("status"),
            "params": {"instruction": body.prompt}})
        # `run_now` has no adapter: the routine runs at its first slot, in its own thread.
        # SAVED EITHER WAY, and told what it will be refused (`routine_needs`).
        needs = routine_needs.needs_person(body.prompt or "")
        out = {"routine": {**card, "needs_person": needs}, "job_id": None, "ran_now": False,
               "flow": made, "needs_person": needs}
        if needs:
            out["warning"] = routine_needs.warning_for(needs)
        return out

    @router.get("/api/routines")
    def list_routines(request: Request, response: Response):
        _adapter(response)
        subject = subject_of(request)
        try:
            flows = routine_flows.FlowsClient().routines(subject)
        except routine_flows.FlowsError as e:
            raise _flows_refusal(e)
        state = routine_flows.load_flow_state(wsr.root, subject)
        gate = _background_gate()
        cards = [{**routine_flows.card_from_flow(subject, f, state=state), **gate} for f in flows]
        for c in cards:
            c["needs_person"] = routine_needs.needs_person(c.get("plan_summary") or "")
            if c["needs_person"]:
                c["warning"] = routine_needs.warning_for(c["needs_person"])
        return {"routines": cards + routine_flows.problem_cards(wsr.root, subject)}

    @router.post("/api/routines/{name}/confirm")
    def confirm_routine(name: str, request: Request, response: Response):
        """Gone: routine files are converted once into flows; a routine an agent wrote is a paused
        flow, started by resuming it (`PATCH …/enabled`)."""
        _adapter(response)
        raise HTTPException(status_code=410, detail={
            "refused": "routine_files_retired",
            "note": "routines are flows; resume a paused routine to start it"})

    @router.patch("/api/routines/{name}/enabled")
    def set_routine_enabled(name: str, body: RoutineEnabledPatch, request: Request,
                            response: Response):
        _adapter(response)
        if body.enabled:
            # TURNING A ROUTINE ON IS THE PERSON'S ACT, from their own session (D24, H8): no agent
            # turn arms one, its own proposals included. Switching one off stays open to it.
            refuse_delegated(request, reason="person_approval_required",
                             instruction="Turning a routine on is your person's act. Tell them it is "
                                         "waiting on their Routines board; do not retry it another way.")
        subject = subject_of(request)
        flow = _flow_routine(subject, routine_flows.flow_name(name))
        if flow is None:
            raise HTTPException(status_code=404, detail="unknown routine")
        try:
            routine_flows.FlowsClient().set_status(
                subject, flow["name"], flow["version"], "resume" if body.enabled else "pause")
        except routine_flows.FlowsError as e:
            raise _flows_refusal(e)
        # switching a routine on clears why it was paused; switching it off is the person's own act
        routine_flows.set_paused_reason(
            wsr.root, subject, flow["name"], None if body.enabled else "paused by you")
        return {"ok": True, "name": flow["name"], "enabled": body.enabled}

    @router.delete("/api/routines/{routine_id}")
    def delete_routine(routine_id: str, request: Request, response: Response):
        _adapter(response)
        subject = subject_of(request)
        if routine_id.startswith(routine_flows.FILE_ID_PREFIX):
            # a card for a routine file the migration could not convert: dismissed (its copy stays)
            name = routine_id[len(routine_flows.FILE_ID_PREFIX):]
            if not routine_flows.dismiss_problem(wsr.root, subject, name):
                raise HTTPException(status_code=404, detail="unknown routine")
            return {"ok": True, "routine_id": routine_id, "dismissed": True}
        flow = _flow_routine(subject, routine_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="unknown routine")
        try:
            routine_flows.FlowsClient().set_status(subject, flow["name"], flow["version"], "retire")
        except routine_flows.FlowsError as e:
            raise _flows_refusal(e)
        return {"ok": True, "routine_id": routine_id, "retired": True}

    return router
