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

So `VEXA_FLOWS_API_URL` on this service is declared as a publish edge in `config.v1.json`, and
`gate:domain-doors` reads it as one: the rule is *a domain's doors are identity, runtime and itself*
for CALLS, and a publish is not a call. Without that distinction the sanctioned coupling mechanism
would fail its own gate, and the only way to satisfy the gate would be to stop telling anyone
anything. Spelled bare `FLOWS_API_URL` until F208 — the only one of the three flows publishers not
carrying the `VEXA_` prefix this repo uses for its own keys, while meeting-api and agent-api already
did. `_flows_base()` below still reads the old name for one release, with a boot warning naming the
new one (`__main__.build_production_app`).

WHAT MAY NEVER HAPPEN is the reverse of a dropped publish: onboarding completing without the fact
being recorded. That is a person who is signed in and has no seat, and nobody finds out until an
invoice. The stamp is written on the person in the same transaction as the account, so the record of
the fact survives a publish that never lands — a later sweep can replay from it.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

EVENT_ONBOARDING_COMPLETED = "onboarding.completed"

log = logging.getLogger("admin_api.events")

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
#:
#: AND THE BOUND IS ONLY REAL BECAUSE THE CALL IS ASYNC. This used to be
#: ``urllib.request.urlopen(req, timeout=TIMEOUT_S)`` — a BLOCKING call on the event loop, made
#: from inside ``async def create_user``. Two separate defects, and the smaller one was the 2 s:
#: urllib's ``timeout`` is a SOCKET timeout, so name resolution is not bounded by it at all — an
#: unresolvable ``VEXA_FLOWS_API_URL`` stalled the whole admin-api process (every other request,
#: ``/internal/validate`` and ``/health`` included) for as long as the resolver took.
#: ``httpx.AsyncClient(timeout=…)`` bounds connect (DNS included), write, read and pool
#: acquisition, and awaits rather than blocks: a slow flows costs THIS sign-in its bound and costs
#: every other request nothing.
TIMEOUT_S = 2.0


#: The canonical name (config.v1.json, F208) — matches meeting-api's and agent-api's own
#: config.v1.json spelling of the same publish-edge key.
FLOWS_API_URL_ENV = "VEXA_FLOWS_API_URL"
#: Honoured for one release, with a boot warning (`__main__.build_production_app`). This is the
#: name every deployment set before F208, when admin-api was the one flows publisher spelling the
#: key bare while meeting-api and agent-api already carried the `VEXA_` prefix.
FLOWS_API_URL_ENV_DEPRECATED = ("FLOWS_API_URL",)


def _flows_base() -> str:
    base = (os.getenv(FLOWS_API_URL_ENV) or "").rstrip("/")
    if base:
        return base
    for legacy in FLOWS_API_URL_ENV_DEPRECATED:
        legacy_base = (os.getenv(legacy) or "").rstrip("/")
        if legacy_base:
            return legacy_base
    return ""


def deprecated_flows_url_env_in_use(env: Optional[dict] = None) -> Optional[str]:
    """The deprecated env var name this process would fall back to, or None.

    A pure check — this module never logs itself (`_flows_base` is called on every onboarding, and
    a warning per request would be noise); `__main__.build_production_app` calls this once at boot
    and logs it there, the same split `config_preflight.preflight()` already uses."""
    env = os.environ if env is None else env
    if (env.get(FLOWS_API_URL_ENV) or "").strip():
        return None
    for legacy in FLOWS_API_URL_ENV_DEPRECATED:
        if (env.get(legacy) or "").strip():
            return legacy
    return None


def _flows_key() -> str:
    return (os.getenv("VEXA_FLOWS_API_KEY") or "").strip()


async def publish(event_type: str, source_event_id: str, subject_refs: dict,
                  *, timeout: Optional[float] = None) -> bool:
    """Hand one fact to the flows intake. Returns whether it landed; NEVER raises.

    AWAITED, not blocking — see ``TIMEOUT_S``. The caller is ``async def create_user``.

    The return value is for a caller that wants to log or count, not for one that wants to decide:
    there is no correct behaviour on a failed publish except to carry on, because the alternative is
    refusing to onboard somebody over a message they never asked us to send."""
    base = _flows_base()
    if not base:
        return False          # no flows domain here — the fact is still recorded on the person
    # `refs` IS THE FIELD NAME ON THE WIRE, and it is not the name this value has in python.
    # flows' intake is `EventSubmission(event_type, source_event_id, refs)` — a plain pydantic
    # BaseModel, so an unknown key is IGNORED rather than refused. This body said `subject_refs`
    # until 2026-09-03, which means every `onboarding.completed` it ever sent was admitted `202`
    # with `refs == {}`: no subject, no org, no seat, on the fact a paid deployment BILLS on. The
    # producer saw success, the intake recorded an admitted fact, and the refs the census promises
    # a consumer were dropped in between. Found by the census suite's
    # `test_every_publisher_names_the_refs_field_the_intake_actually_reads`, which derives both
    # halves from source precisely because a publish edge has no shared type to disagree with.
    body = json.dumps({"event_type": event_type, "source_event_id": source_event_id,
                       "refs": subject_refs}).encode()
    headers = {"content-type": "application/json"}
    key = _flows_key()
    if key:
        # X-Flows-OPERATOR-Key: this is flows-api's own operator key (VEXA_FLOWS_API_KEY, read
        # above), never admin-api's token. The header used to be spelled `X-Flows-Admin-Key`, and
        # naming this service's own token in another service's header is precisely how a lane came
        # to run on `changeme`. flows accepts the old name for one release.
        headers["X-Flows-Operator-Key"] = key
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT_S) as client:
            r = await client.post(f"{base}/events", content=body, headers=headers)
        return 200 <= r.status_code < 300
    except Exception:  # noqa: BLE001 — see the module docstring: a publish is not a dependency
        # SWALLOWED, NOT SILENT. The old shape returned False from a bare `except` and left no
        # trace, so a flows address that had been wrong since a deploy was indistinguishable from a
        # deployment with no flows domain — on the one fact a paid deployment bills on. DEBUG
        # because the person is already committed and a sweep can replay from the stamp.
        log.debug("flows publish %s did not land (base=%s)", event_type, base, exc_info=True)
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
