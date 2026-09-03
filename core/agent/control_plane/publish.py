"""PUBLISHING A DESK FACT INTO FLOWS — the agent domain tells, it does not ask.

Two carriers live here, both of them things only this domain knows: a desk exists and nobody has
finished setting it up (`desk.unscaffolded`), and an agent has written down something it believes
about a person's company and needs that person's word on it (`claim.proposed`). PRD ruling 9 — desk
cards are agent events in the FULL profile. `#1482` registered both event types in flows with
`desk_setup` and `desk_claim` behind them, and left the producer's side empty: the definitions could
react to both facts and nothing in the repository published either.

A PUBLISH EDGE IS NOT A DEPENDENCY, and that distinction is the whole design of this file — it is
identity's `admin_api/app/events.py` argument, applied to the domain one layer up:

  * a DEPENDENCY is a call whose answer the caller needs. This module has none: nothing it does
    changes what agent-api answers, and no caller of it branches on the result.
  * a PUBLISH is a fact handed over. Best-effort, bounded, swallowed. A deployment with no flows
    domain provisions desks and records claims exactly as one with flows does; so does one where
    flows is down or slow. That is a PROFILE, not a degraded state.

WHY THIS IS NOT `control_plane/events.py`, which already exists: that module is the INBOUND mapper
(`event.v1` → a `unit.v1` dispatch) — facts arriving to be worked. This is the outbound half.
One file per direction, because a reader who finds them merged cannot tell which way a fact travels.

WHAT MAY NEVER HAPPEN is the reverse of a dropped publish: the desk state changing without the fact
being TRUE on disk. It cannot, here, because neither publish invents anything — `desk.unscaffolded`
says a workspace directory exists with no `.scaffolded` in it, and `claim.proposed` says a row is in
the claim book. Both are re-derivable from the desk itself, so a publish that never lands loses a
queue card and never loses the underlying fact; a sweep can replay from the desk.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

EVENT_DESK_UNSCAFFOLDED = "desk.unscaffolded"
EVENT_CLAIM_PROPOSED = "claim.proposed"

#: Bounded on purpose. Both publishes run INSIDE a request a person is waiting on — a sign-in that
#: provisions a desk, and an agent turn writing what it learned — so the ceiling on how slow flows
#: can make those is this number, not flows' own timeout.
TIMEOUT_S = 2.0


def _flows_base() -> str:
    return (os.environ.get("VEXA_FLOWS_API_URL") or "").rstrip("/")


def _flows_key() -> str:
    """The OPERATOR key. flows' `POST /events` authenticates on `X-Flows-Admin-Key`, and the
    read-only `VEXA_FLOWS_TIMELINE_KEY` opens `GET /timeline` and nothing else — so the timeline
    key cannot stand in here, and this is deliberately not a fallback chain: a publish sent with a
    credential that cannot admit it is a publish that always 401s, which looks exactly like a
    deployment that runs no flows and is not one."""
    return (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()


def publish(event_type: str, source_event_id: str, subject_refs: dict,
            *, timeout: Optional[float] = None) -> bool:
    """Hand one fact to the flows intake. Returns whether it landed; NEVER raises.

    The return value is for a caller that wants to log or count, not for one that wants to decide:
    there is no correct behaviour on a failed publish except to carry on, because the alternative is
    refusing to provision somebody's desk over a message they never asked us to send."""
    base = _flows_base()
    if not base:
        return False          # no flows domain here — the desk still carries the fact
    body = json.dumps({"event_type": event_type, "source_event_id": source_event_id,
                       "subject_refs": subject_refs}).encode()
    headers = {"content-type": "application/json"}
    key = _flows_key()
    if key:
        headers["X-Flows-Admin-Key"] = key
    req = urllib.request.Request(f"{base}/events", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT_S) as r:  # noqa: S310
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001 — see the module docstring: a publish is not a dependency
        return False


def desk_source_id(subject) -> str:
    """The fact's id, keyed to the PERSON — because the fact is about their one desk.

    flows admits on `(source_event_id, flow)`, so a re-delivery is a no-op there. That matters more
    here than the caller's own guard does: `POST /api/workspace/init` is called on EVERY login and
    is idempotent by design, so the only thing standing between "one card" and "one card per
    sign-in, forever" is that this id does not change."""
    return f"desk-{subject}"


def claim_source_id(subject, claim_id) -> str:
    """Keyed to (person, claim). ONE EVENT PER CLAIM, never one per call: `await_claim` looks a
    `claim_id` up in the book and blocks on that claim's own words, so a single event for a batch
    of three would put one card in front of a person for three questions with no way to answer two
    of them."""
    return f"claim-{subject}-{claim_id}"


def desk_refs(subject) -> dict:
    """`{uid}` — the ref `await_scaffold` requires, and the spelling flows uses for a person
    everywhere in the production definitions."""
    return {"uid": str(subject)}


def claim_refs(subject, claim_id) -> dict:
    """`{uid, claim_id}` — both required by `await_claim`, which fails typed and non-retryable
    without either: *"without both there is nothing to look up and nothing to resolve"*."""
    return {"uid": str(subject), "claim_id": str(claim_id)}
