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
import urllib.error
import urllib.parse
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
    """The OPERATOR key. flows' `POST /events` authenticates on `X-Flows-Operator-Key`, and the
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
    # `refs` IS THE FIELD NAME ON THE WIRE, and it is the one thing in this file that cannot be
    # read off the python. flows' intake model is `EventSubmission(event_type, source_event_id,
    # refs)` (`flows_integrations/flows_api.py`) — a plain pydantic BaseModel, so it IGNORES an
    # unknown key instead of refusing it. A body spelled `subject_refs` is therefore ADMITTED, 202,
    # with `refs == {}`, and `await_scaffold` then raises *"desk.unscaffolded carried no uid"*
    # non-retryably on every card. Success at the intake, nothing on the queue, and no error
    # anywhere between them: the one failure on this path that reports itself as working.
    body = json.dumps({"event_type": event_type, "source_event_id": source_event_id,
                       "refs": subject_refs}).encode()
    headers = {"content-type": "application/json"}
    key = _flows_key()
    if key:
        # The OPERATOR key's canonical header since #1486. flows still reads the old
        # `X-Flows-Admin-Key` for one release and it is deliberately not sent: a deprecated
        # spelling that keeps working is how a rename never finishes.
        headers["X-Flows-Operator-Key"] = key
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


# --- friction: a direct HTTP client of flows OWN /friction route (#1510) -------------------
# NOT the generic /events intake above, and deliberately so. `friction.reported`s producing
# domain is FLOWS ITSELF -- flows-api's POST /friction admits it in-process, no publish-edge,
# no config.v1 declaration on that side at all (see the carrier's own census entry in
# core/flows/contracts/flows.v1/carriers.json) -- and a carrier has exactly ONE producing
# domain (gate:config-contract enforces this against every service's config.v1 declaration,
# not only this one's: "a carrier has exactly ONE producing domain, and a second producer is
# how a consumer that must act once acts twice"). So agent-api's own `POST /api/friction`
# (`routers/friction.py`) and the refused-model-endpoint path (`Dispatcher.attach_friction`)
# are HTTP CLIENTS of flows' existing route -- exactly the same shape the rig's
# `report_friction` already uses (`deploy/dogfood/rig/vexa_control_mcp.py`) -- never a second
# producer registered anywhere. This reuses the identical VEXA_FLOWS_API_URL/VEXA_FLOWS_API_KEY
# pair desk.unscaffolded/claim.proposed already declare as a publish edge: same deploy surface,
# same credential, a different kind of call over it.

FRICTION_TIMEOUT_S = 2.0


def post_friction(rec: dict, *, deployment: str = "", worker_image: str = "") -> tuple[bool, dict]:
    """POST straight onto flows' `/friction` route. Query parameters, not a JSON body --
    `flows_integrations/flows_api.py`'s `report_friction` has no `Body(...)` marker on any of
    its arguments, the same convention `reactions_list`/`timeline` already read on this surface.
    Returns `(published, body)` -- `body` is flows' own response (`{id, recorded}`) when it
    landed, `{}` otherwise. NEVER raises: a friction report is never worth failing the caller's
    own request over (the worker's turn, the terminal's "Report this", a refused model
    endpoint).

    `rec` is `shared.friction`-normalized (`tried`/`happened`/`session`/`severity`/`subject`/
    `context`). `deployment`/`worker_image` are not part of that shape's CONTEXT_KEYS, so a
    caller that has them passes them in separately rather than losing them to normalize()'s
    "everything unknown is dropped" rule.
    """
    base = _flows_base()
    key = _flows_key()
    if not base or not key:
        return False, {}          # no flows domain here, or no operator key minted
    ctx = rec.get("context") or {}
    params = {k: v for k, v in {
        "session": rec.get("session") or "",
        "what_i_tried": rec.get("tried") or "",
        "what_happened": rec.get("happened") or "",
        "severity": rec.get("severity") or "annoyance",
        "meeting_id": ctx.get("meeting_id") or "",
        "tool": ctx.get("tool") or "",
        "kind": rec.get("kind") or "",
        "deployment": deployment,
        "worker_image": worker_image,
    }.items() if v}
    url = f"{base}/friction?{urllib.parse.urlencode(params)}"
    headers = {"X-Flows-Operator-Key": key}
    uid = str(rec.get("subject") or "")
    if uid:
        headers["X-User-Id"] = uid
    req = urllib.request.Request(url, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=FRICTION_TIMEOUT_S) as r:  # noqa: S310
            try:
                body = json.loads(r.read().decode() or "{}")
            except (TypeError, ValueError):
                body = {}
            return 200 <= r.status < 300, body
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        return False, body
    except Exception:  # noqa: BLE001 -- see the module docstring: a publish is not a dependency
        return False, {}


def file_friction_report(record: dict) -> bool:
    """Normalize a raw friction record (shared.friction's shape) and forward it onto flows' own
    /friction route -- the in-process counterpart of routers/friction.py's HTTP route, for the
    one caller that already holds a python dict rather than an HTTP body: a refused model
    endpoint (model_endpoint.refusal_friction, wired in via Dispatcher.attach_friction -- see
    #1510's C1/C5, replacing the old FrictionStore.file wiring)."""
    from shared import friction as friction_mod

    rec = friction_mod.normalize(record)
    ok, _resp = post_friction(rec)
    return ok
