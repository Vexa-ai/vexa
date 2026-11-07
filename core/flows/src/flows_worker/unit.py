"""THE WORKER'S UNIT OF WORK, traced (logevent.v1).

The worker serves no requests, so nothing hands it a trace: it mints one per unit of work — one
`tick`, which claims and runs at most ONE step of one reaction — and binds it for the duration.
Every line the unit emits and every call it makes to a Vexa service (`flows_steps.common.http`
forwards `X-Trace-Id`) share that id, so agent-api, meeting-api and identity log the same trace as
the step that called them. The reaction id goes in `fields`: a reaction spans many units, and that
is the key that collects them.

The engine (`flows/`) is stdlib-pure and knows nothing of logging, so the step-level lines come from
wrapping the registry's step functions here, in the process that owns them, after composition.
Lines carry ids and outcomes only — never refs, scratch, mail text or an error's message (a
StepError's text can carry a URL with an address in it); the exception CLASS is enough to find it.
"""
from __future__ import annotations

import functools

import flows_obs
from flows import tick

SERVICE = "flows-worker"


def _wrap(name: str, fn):
    @functools.wraps(fn)
    def traced(ctx):
        r = ctx.reaction
        refs = r.subject_refs if isinstance(r.subject_refs, dict) else {}
        base = {"reaction_id": r.reaction_id, "flow": r.flow, "flow_version": r.flow_version,
                "step": name, "attempt": r.attempt, "event_type": r.event_type}
        uid, meeting_id = refs.get("uid"), refs.get("meeting_id")
        flows_obs.set_user_id(uid if uid not in (None, "") else None)
        flows_obs.log_event("step_dispatched", meeting_id=meeting_id, fields=base)
        try:
            out = fn(ctx)
        except Exception as e:
            flows_obs.log_event("step_failed", level="warning", meeting_id=meeting_id,
                                fields={**base, "error": type(e).__name__,
                                        "retryable": bool(getattr(e, "retryable", True))})
            raise
        flows_obs.log_event("step_outcome", meeting_id=meeting_id,
                            fields={**base, "outcome": type(out).__name__})
        return out
    traced.__flows_traced__ = True
    return traced


def instrument(reg) -> None:
    """Wrap every registered step so it logs `step_dispatched` / `step_outcome` / `step_failed`.
    Called once, after `production.build`; idempotent."""
    for name, fn in list(reg.steps.items()):
        if not getattr(fn, "__flows_traced__", False):
            reg.steps[name] = _wrap(name, fn)


def run_unit(db, reg, clock, *, emit=None, present=None) -> bool:
    """One traced unit: bind a fresh trace, run one `tick`, return whether it worked."""
    with flows_obs.unit(SERVICE):
        return tick(db, reg, clock, emit=emit, present=present)
