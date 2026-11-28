"""The account erase's reach into the meetings domain (P15: a person erases their own account,
nothing left, with a receipt).

``DELETE /admin/users/{id}`` asks meeting-api to erase every meeting of the person through
meeting-api's own internal door, ``DELETE /internal/users/{id}/meetings``, BEFORE it deletes
anything of its own. The two services share a database, but the meetings tables are meeting-api's:
it owns their objects in storage (recordings, session tapes), their Redis keys and the stop of a
live bot, none of which a SQL delete from here could reach. If meeting-api does not confirm, this
service deletes nothing, so a retry is safe.

Stripe is not called here (a later slice). The person's Stripe ids are named in the receipt so the
caller knows what is still held there.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, status

from .internal_tier import internal_secret

#: meeting-api's door (config.v1 capability `account_meetings_erase`). Unset: no meetings domain.
MEETING_API_URL_ENV = "MEETING_API_URL"
#: How long meeting-api waits for a live bot to leave before it answers 409 (its cap is 60).
STOP_WAIT_S = 20.0
#: This hop's own bound: the stop wait plus the deletes themselves.
TIMEOUT_S = STOP_WAIT_S + 40.0
#: The Stripe ids an account carries in ``users.data`` (``PlatformBillingDataPatch``).
STRIPE_KEYS = ("stripe_customer_id", "stripe_subscription_id", "stripe_tx_subscription_id",
               "stripe_payment_method_id")


def meeting_api_url() -> str:
    return (os.environ.get(MEETING_API_URL_ENV) or "").strip().rstrip("/")


def stripe_ids(data) -> dict:
    data = data if isinstance(data, dict) else {}
    return {key: data[key] for key in STRIPE_KEYS if data.get(key)}


async def erase_meetings(user_id: int):
    """meeting-api's erase of every meeting of ``user_id``: its per-kind counts, or ``"not_present"``
    when no meetings domain is deployed. Raises 409 when a bot did not leave in time and 502 when
    meeting-api did not confirm — nothing was deleted."""
    base = meeting_api_url()
    if not base:
        return "not_present"
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.delete(f"{base}/internal/users/{int(user_id)}/meetings",
                                    params={"wait_s": STOP_WAIT_S},
                                    headers={"X-Internal-Secret": internal_secret()})
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail="meeting-api could not erase this person's meetings; nothing was deleted") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if r.status_code == 409:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "bot_still_running", "running": body.get("running") or [],
            "detail": "A bot of this person is still in a meeting; nothing was deleted. Retry once it has left."})
    if r.status_code != 200 or not isinstance(body.get("deleted"), dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail=f"meeting-api did not confirm the erase ({r.status_code}); nothing was deleted")
    return body["deleted"]
