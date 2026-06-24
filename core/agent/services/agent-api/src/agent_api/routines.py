"""routines.py — the routine compiler (the authoring → execution bridge).

A ``routine.v1`` Routine is what the user authors (``/routine`` in chat, or the Routines surface). It
COMPILES DOWN to a ``schedule.v1`` job in the runtime's durable cron whose ``request`` POSTs a
``unit.v1`` Invocation back to agent-api ``/invocations`` when due — so a scheduled routine is just the
same agent-runtime-unit fired on a clock instead of a chat message. The runtime owns the cron (re-arm,
retry, idempotency); agent-api only authors jobs (P7 — no in-process timer).

This module is PURE (no I/O): it builds the routine, the Invocation, and the job spec, and reads a job
back into a routine card for listing. The scheduler is the registry of record for scheduled routines —
the job's ``metadata`` carries enough to render the card, so MVP2 needs no separate routine store
(routine-as-git-entity, triaged like the rest of the graph, is the MVP5 enhancement — see DECISIONS).
"""
from __future__ import annotations

import hashlib
from typing import Optional

from . import contracts


def routine_id_for(subject: str, name: str, cron: str) -> str:
    """A stable routine id — same (subject, name, cron) → same id, so re-authoring DEDUPS (the
    scheduler's idempotency_key is this id)."""
    digest = hashlib.sha1(f"{subject}|{name}|{cron}".encode()).hexdigest()[:10]
    return f"rt_{digest}"


def make_routine(
    *,
    subject: str,
    name: str,
    cron: str,
    prompt: Optional[str] = None,
    plan_ref: Optional[str] = None,
    lifecycle: str = "oneshot",
    routine_id: Optional[str] = None,
) -> dict:
    """Build a ``routine.v1`` Routine from a simple create form. Validates at the seam (P8)."""
    if not (prompt or plan_ref):
        raise ValueError("a routine needs either a plan prompt or a plan ref")
    plan = {"ref": plan_ref} if plan_ref else {"prompt": prompt}
    routine = {
        "id": routine_id or routine_id_for(subject, name, cron),
        "owner": subject,
        "name": name,
        "trigger": {"kind": "scheduled", "cron": cron},
        "plan": plan,
        "lifecycle": lifecycle,
        "enabled": True,
    }
    contracts.validate_routine(routine)  # fail loud on a non-conformant routine
    return routine


def build_invocation(routine: dict, *, workspace_repo: str, workspace_ref: str = "main") -> dict:
    """Compile a Routine into the ``unit.v1`` Invocation fired each time it runs (trigger=scheduled)."""
    invocation = {
        "trigger": "scheduled",
        "subject": routine["owner"],
        "workspace_repo": workspace_repo,
        "workspace_ref": workspace_ref,
        "context": {"kind": "generic"},
        "plan": dict(routine["plan"]),
        "lifecycle": routine.get("lifecycle", "oneshot"),
        "output": {"topic": f"unit:{routine['id']}:out", "modes": ["sse"]},
        "tools": [],
    }
    contracts.validate_unit_invocation(invocation)  # the body we POST is unit.v1-conformant
    return invocation


def compile_to_job(routine: dict, *, invocations_url: str, workspace_repo: str) -> dict:
    """Compile a Routine into a ``schedule.v1`` job: the cron, and the HTTP request that fires the
    Invocation at agent-api ``/invocations``. The job ``metadata`` carries the routine summary so the
    Routines surface can list it straight from the scheduler (no separate store)."""
    invocation = build_invocation(routine, workspace_repo=workspace_repo)
    plan = routine["plan"]
    return {
        "cron": routine["trigger"]["cron"],
        "request": {"method": "POST", "url": invocations_url, "body": invocation},
        "idempotency_key": routine["id"],
        "metadata": {
            "routine_id": routine["id"],
            "owner": routine["owner"],
            "name": routine["name"],
            "cron": routine["trigger"]["cron"],
            "kind": "scheduled",
            "lifecycle": routine.get("lifecycle", "oneshot"),
            "plan_kind": "ref" if plan.get("ref") else "prompt",
            "plan_summary": plan.get("ref") or plan.get("prompt", ""),
        },
    }


def routine_card_from_job(job: dict) -> Optional[dict]:
    """Read a schedule.v1 job back into a routine card for the Routines surface, or None if the job is
    not a routine (no routine_id in metadata). ``next_run`` is the job's next fire time."""
    meta = job.get("metadata") or {}
    rid = meta.get("routine_id")
    if not rid:
        return None
    return {
        "id": rid,
        "owner": meta.get("owner"),
        "name": meta.get("name"),
        "cron": meta.get("cron"),
        "kind": meta.get("kind", "scheduled"),
        "lifecycle": meta.get("lifecycle", "oneshot"),
        "plan_kind": meta.get("plan_kind"),
        "plan_summary": meta.get("plan_summary"),
        "job_id": job.get("job_id"),
        "next_run": job.get("execute_at"),
        "status": job.get("status"),
    }
