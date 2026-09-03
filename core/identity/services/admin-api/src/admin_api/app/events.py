"""PUBLISHING A FACT INTO FLOWS — identity tells, it does not ask.

`onboarding.completed` is the fact this module exists for. A person entering is identity's to know,
and a paid deployment bills on it (founder ruling, 2026-09-02), so it is a contract rather than a
convenience: ONE producer, an exact payload, exactly once, in every configuration, with no other
domain's code on the path.

A PUBLISH EDGE IS NOT A DEPENDENCY, and that distinction is the whole design of this file:

  * a DEPENDENCY is a call whose answer the caller needs. Identity has none — it depends on nothing,
    which is what makes every other domain able to depend on IT.
  * a PUBLISH is a fact handed over. It is best-effort, bounded, and swallowed. A deployment with no
    flows domain still onboards people; so does one where flows is down or slow.

So `FLOWS_API_URL` on this service is declared as a publish edge in `config.v1.json`, and
`gate:domain-doors` reads it as one: the rule is *a domain's doors are identity, runtime and itself*
for CALLS, and a publish is not a call. Without that distinction the sanctioned coupling mechanism
would fail its own gate, and the only way to satisfy the gate would be to stop telling anyone
anything.

WHAT MAY NEVER HAPPEN is the reverse of a dropped publish: onboarding completing without the fact
being recorded. That is a person who is signed in and has no seat, and nobody finds out until an
invoice. The stamp is written on the person in the same transaction as the account, so the record of
the fact survives a publish that never lands — a later sweep can replay from it.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

EVENT_ONBOARDING_COMPLETED = "onboarding.completed"

#: The org this deployment's identity knows for a person: none. Identity has no organisation
#: concept — no column, no create field, nothing — so the ref is emitted EMPTY rather than omitted.
#: A missing key cannot be told apart from a key nobody looked up, and the consumer that cannot
#: tell will infer one from the email domain, which is a second place the answer lives. Named, so
#: that the day identity does hold an org there is one place to change and a test that fails.
NO_ORG = ""

#: Every person onboarded through this door gets the same seat. There is no seat model in identity
#: either; a billing domain that needs tiers reads them from wherever it prices, not from here.
DEFAULT_SEAT = "member"

#: Bounded on purpose. This runs inside the request that creates a person, so the ceiling on how
#: slow flows can make sign-in is this number, not flows' own timeout.
TIMEOUT_S = 2.0


def _flows_base() -> str:
    return (os.getenv("FLOWS_API_URL") or "").rstrip("/")


def _flows_key() -> str:
    return (os.getenv("VEXA_FLOWS_API_KEY") or "").strip()


def publish(event_type: str, source_event_id: str, subject_refs: dict,
            *, timeout: Optional[float] = None) -> bool:
    """Hand one fact to the flows intake. Returns whether it landed; NEVER raises.

    The return value is for a caller that wants to log or count, not for one that wants to decide:
    there is no correct behaviour on a failed publish except to carry on, because the alternative is
    refusing to onboard somebody over a message they never asked us to send."""
    base = _flows_base()
    if not base:
        return False          # no flows domain here — the fact is still recorded on the person
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


def onboarding_source_id(subject) -> str:
    """The fact's id, keyed to the PERSON.

    flows admits on `(source_event_id, flow)`, so a re-delivery of this fact is a no-op there as
    well as here. Two guards for one thing, deliberately: a double charge is not something an
    apology takes back."""
    return f"onboarding-{subject}"


def onboarding_refs(subject, org, seat) -> dict:
    """`{subject, org, seat}` — the founder's three fields.

    `seat` is what a billing domain charges for and `org` is what it charges; both are STATED here
    rather than left for a consumer to infer, because a consumer that infers them is a second place
    the answer lives."""
    return {"subject": str(subject), "org": str(org or ""), "seat": str(seat or "member")}
