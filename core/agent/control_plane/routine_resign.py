"""routine_resign.py — the routines armed before dispatches were signed, re-armed signed, once.

`POST /invocations` runs a dispatch only from the internal tier or with the signature agent-api puts
on a routine job when it compiles it (`dispatch_sink.py`). A routine created through
`POST /api/routines` before that existed sits in the runtime's scheduler with no signature, and every
fire of it would be refused (a 4xx the scheduler does not retry). Workspace-file routines need nothing
from here: the reconciler re-arms them from their files. This module re-arms the others.

WHAT IT WILL RE-SIGN, and why it is narrow. The scheduler's store is not this service's to trust: a
job in it is only a claim that agent-api once compiled it. So a job is re-signed only when its body is
exactly what agent-api's own compiler produces from the job's own record (owner, name, cron, plan,
routine id) — a scheduled, unwatched turn for that owner on that plan, nothing wider — apart from
`runner`, which follows the deployment. Anything else is left alone and logged. And it runs ONCE: the
marker lives in the store's dot-namespace (`<store-root>/.routines/`), outside every workspace mount,
so after the first complete pass nothing can ask for a job to be blessed again. Every re-armed routine
is logged by id and owner.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from control_plane import dispatch_sink
from control_plane import routines as routines_mod
from shared.ports import SchedulerPort

log = logging.getLogger(__name__)

#: The store's dot-namespace for routine bookkeeping (`workspace_membership`'s `.invites` precedent).
STATE_DIR = ".routines"
MARKER = "dispatch-signature.v1.json"
#: Workspace-file routines; the reconciler re-arms these itself.
_WORKSPACE_SOURCE = "workspace-routine"


def _has_signature(request: dict) -> bool:
    return any(str(k).lower() == dispatch_sink.HEADER.lower() and v
               for k, v in (request.get("headers") or {}).items())


def _without_runner(body: dict) -> bytes:
    return dispatch_sink.canonical({k: v for k, v in dict(body).items() if k != "runner"})


def _expected_body(meta: dict, cron: str) -> Optional[dict]:
    """What this service's compiler produces from the job's own record, or None if it cannot."""
    owner, name, rid = meta.get("owner"), meta.get("name"), meta.get("routine_id")
    summary, kind = meta.get("plan_summary") or "", meta.get("plan_kind")
    if not (owner and name and rid and cron and summary):
        return None
    try:
        routine = routines_mod.make_routine(
            subject=str(owner), name=str(name), cron=str(cron), routine_id=str(rid),
            lifecycle=str(meta.get("lifecycle") or "oneshot"),
            prompt=None if kind == "ref" else str(summary),
            plan_ref=str(summary) if kind == "ref" else None)
        return routines_mod.build_invocation(routine)
    except Exception:  # noqa: BLE001 — a record this compiler cannot rebuild is not ours to bless
        return None


def resign_unsigned_routines(scheduler: SchedulerPort, *, invocations_url: str,
                             signing_secret: str, store_root: str | Path) -> Optional[list[str]]:
    """Re-arm, signed, the routine jobs compiled before dispatches were signed. Returns the routine
    ids re-armed, or None when the pass is already done (or cannot run: no secret)."""
    marker = Path(store_root) / STATE_DIR / MARKER
    if not signing_secret or marker.exists():
        return None
    rearmed: list[str] = []
    in_flight = 0
    for job in scheduler.list_jobs(limit=1000):
        request = job.get("request") or {}
        meta = job.get("metadata") or {}
        if request.get("url") != invocations_url or _has_signature(request):
            continue
        if meta.get("source") == _WORKSPACE_SOURCE or not meta.get("routine_id"):
            continue
        if job.get("status") not in (None, "pending"):
            in_flight += 1  # executing now; it re-arms unsigned, so the next pass takes it
            continue
        body = request.get("body")
        expected = _expected_body(meta, str(job.get("cron") or ""))
        if not isinstance(body, dict) or expected is None or _without_runner(body) != _without_runner(expected):
            log.warning("routine %s (owner %s) left unsigned: its job is not what this service "
                        "compiles from its own record", meta.get("routine_id"), meta.get("owner"))
            continue
        signed = {
            "cron": job.get("cron"),
            "request": {"method": request.get("method") or "POST", "url": invocations_url,
                        "body": body,
                        "headers": {dispatch_sink.HEADER: dispatch_sink.sign(signing_secret, body)}},
            # A fresh key: the routine's own id still maps to the job being replaced.
            "idempotency_key": f"{meta['routine_id']}:signed-v1",
            "metadata": dict(meta),
        }
        scheduler.cancel_job(job["job_id"])
        scheduler.schedule(signed)
        rearmed.append(str(meta["routine_id"]))
        log.warning("routine %s (owner %s) re-armed with a dispatch signature: it was compiled "
                    "before dispatches were signed", meta["routine_id"], meta.get("owner"))
    if in_flight:
        log.warning("%d unsigned routine job(s) were running; the signing pass runs again", in_flight)
        return rearmed
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": time.time(), "rearmed": rearmed}) + "\n")
    return rearmed
