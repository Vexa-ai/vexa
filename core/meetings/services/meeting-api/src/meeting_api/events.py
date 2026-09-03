"""PUBLISHING MEETING FACTS INTO FLOWS — meetings tells, it does not ask.

F168/F181: an ad hoc bot — one started via the MCP `request_meeting_bot` verb, never through a
calendar invite — never produced a `post_meeting` reaction. Only `invite_intake` (flows' own flow)
ever told flows that a meeting started or finished (`emit_started` / `emit_completed`,
`flows_defs/production.py`, PRD decision 42.2); meeting-api's only outbound door was the operator
webhook (`webhooks/system.py`), and flows does not read it. This module is the missing edge:
meeting-api telling flows itself, the way identity's `admin_api/app/events.py` already tells it
about onboarding and agent-api's `control_plane/publish.py` already tells it about desk facts. Copy
those two files' argument before you copy their code — it is the same argument one layer down.

A PUBLISH EDGE IS NOT A DEPENDENCY:

  * a DEPENDENCY is a call whose answer the caller needs. This module has none — nothing the
    lifecycle callback does branches on whether flows heard about it.
  * a PUBLISH is a fact handed over. Best-effort, bounded, swallowed. A deployment with no flows
    domain runs meetings exactly as one with a flows domain does, and so does one where flows is
    down or slow — that is a PROFILE, not a degraded state.

So `VEXA_FLOWS_API_URL` / `VEXA_FLOWS_API_KEY` are declared as a `publish-edge` in `config.v1.json`,
matching agent-api's own spelling of the URL key (identity's admin-api spelled it bare
`FLOWS_API_URL` until F208, which aligned it to the name the other two publishers already used; the
bare name is still honoured there for one release, with a boot warning).

THE SOURCE_EVENT_ID MATCHES FLOWS' OWN SCHEME ON PURPOSE (`live-<meeting_id>` / `done-<meeting_id>`
— see `meeting_started_source_id` / `meeting_completed_source_id`), not a meeting-api-flavoured
id. flows admits on `(source_event_id, flow)` (`flows/admission.py`): the same id from two
producers is one reaction, not two. `invite_intake` still runs its own `emit_started` /
`emit_completed` for calendar-intake meetings, and will until it is retired — matching ids is what
lets this ship as a SECOND, redundant, self-deduping producer today rather than requiring that
retirement first. A meeting-api-flavoured id would double-fire `post_meeting` on every calendar
meeting the day this lands.

WHAT THIS BUYS AND WHAT IT DOES NOT: it makes an ad hoc meeting reach `live_meeting` /
`post_meeting` for the first time. It does NOT carry an invite's `participants` / `group` —
meeting-api's domain holds no invite, so it cannot state who was invited, only who spawned the
bot (`uid`) and which call it was (`meeting_id` / `native` / `platform`). For a calendar-intake
meeting the DUAL-PRODUCER race usually resolves in THIS module's favour on `meeting.completed` (it
fires the instant the DB row flips to `completed`; `invite_intake`'s own `emit_completed` only
fires after its next poll tick) — so `process_meeting`'s room-read (`flows_steps/meeting.py
room_order`) degrades to an EMPTY room rather than to invite order on that path, until
`invite_intake`'s producer is retired or this module is taught to carry participants too. Flagged,
not silently absorbed — see the filed issue.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

EVENT_MEETING_STARTED = "meeting.started"
EVENT_MEETING_COMPLETED = "meeting.completed"

#: Bounded on purpose. Both publishes run inside the lifecycle callback a bot's own HTTP round
#: trip is waiting on, so the ceiling on how slow flows can make that callback is this number, not
#: flows' own timeout.
TIMEOUT_S = 2.0


def _flows_base() -> str:
    return (os.getenv("VEXA_FLOWS_API_URL") or "").rstrip("/")


def _flows_key() -> str:
    return (os.getenv("VEXA_FLOWS_API_KEY") or "").strip()


def publish(event_type: str, source_event_id: str, refs: dict,
            *, timeout: Optional[float] = None) -> bool:
    """Hand one fact to the flows intake. Returns whether it landed; NEVER raises.

    The return value is for a caller that wants to log or count, not one that wants to decide:
    there is no correct behaviour on a failed publish except to carry on, because the alternative
    is failing a bot's own lifecycle callback over a message it never asked us to send.
    """
    base = _flows_base()
    if not base:
        return False          # no flows domain here — the meeting still ran, nothing to tell
    # `refs` IS THE FIELD NAME ON THE WIRE (flows_integrations/flows_api.py `EventSubmission`), a
    # plain pydantic BaseModel that IGNORES an unknown key rather than refusing it — the F142 shape,
    # where a body spelled `subject_refs` was admitted 202 with `refs == {}` and every consumer
    # step failed non-retryably on a missing ref while everything upstream looked ordinary. Spelled
    # right here on purpose; `test_every_publisher_names_the_refs_field_the_intake_actually_reads`
    # (flows' carrier census suite) derives both halves from source and would catch a regression.
    body = json.dumps({"event_type": event_type, "source_event_id": source_event_id,
                       "refs": refs}).encode()
    headers = {"content-type": "application/json"}
    key = _flows_key()
    if key:
        # flows-api's OPERATOR key — flows' own POST /events is authenticated on it, never this
        # service's own token. The header used to be spelled X-Flows-Admin-Key; flows accepts the
        # old name for one release and this producer sends only the current one.
        headers["X-Flows-Operator-Key"] = key
    req = urllib.request.Request(f"{base}/events", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT_S) as r:  # noqa: S310
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001 — see the module docstring: a publish is not a dependency
        return False


def meeting_started_source_id(meeting_id) -> str:
    """Matches `invite_intake`'s own `emit_started` id exactly — see the module docstring."""
    return f"live-{meeting_id}"


def meeting_completed_source_id(meeting_id) -> str:
    """Matches `invite_intake`'s own `emit_completed` id exactly — see the module docstring."""
    return f"done-{meeting_id}"


def meeting_started_refs(meeting_id, native, platform, uid) -> dict:
    """`{uid, meeting_id, native, platform}` — `uid` + `meeting_id` are the golden carrier's
    stated minimum (`Carrier.meeting-started.json`); `native` + `platform` ride along because
    meeting-api already has them and a later consumer costs nothing carrying them now."""
    return {"uid": str(uid), "meeting_id": str(meeting_id), "native": str(native or ""),
            "platform": str(platform or "")}


def meeting_completed_refs(meeting_id, native, platform, uid, completion_reason) -> dict:
    """`{uid, meeting_id, native, platform, completion_reason}` — `uid`, `meeting_id` and `native`
    are what `post_meeting`'s `process_meeting` step reads without a `.get()` fallback
    (`flows_defs/production.py`); `platform` and `completion_reason` ride along as extra context
    no step currently requires but a future one, or a human reading the reaction row, might."""
    return {"uid": str(uid), "meeting_id": str(meeting_id), "native": str(native or ""),
            "platform": str(platform or ""), "completion_reason": str(completion_reason or "")}


def publish_meeting_started(meeting_id, native, platform, uid,
                             *, timeout: Optional[float] = None) -> bool:
    return publish(EVENT_MEETING_STARTED, meeting_started_source_id(meeting_id),
                    meeting_started_refs(meeting_id, native, platform, uid), timeout=timeout)


def publish_meeting_completed(meeting_id, native, platform, uid, completion_reason,
                               *, timeout: Optional[float] = None) -> bool:
    return publish(EVENT_MEETING_COMPLETED, meeting_completed_source_id(meeting_id),
                    meeting_completed_refs(meeting_id, native, platform, uid, completion_reason),
                    timeout=timeout)
