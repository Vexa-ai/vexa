"""The account erase's reach into the meetings domain (P15: a person erases their own account,
nothing of theirs left, with a receipt — never other people's or a company's records, and nothing
that moves money).

``DELETE /admin/users/{id}`` asks meeting-api to erase every meeting of the person through
meeting-api's own internal door, ``DELETE /internal/users/{id}/meetings``. The two services share a
database, but the meetings tables are meeting-api's: it owns their objects in storage (recordings,
session tapes), their Redis keys, the stop of a live bot and the rule for which rows are the
person's (a room's row keyed to them stays). Identity only reads that table to refuse an erase it
cannot complete (``meeting_rows``).

Stripe is not called here (a later slice). The person's Stripe ids are named in the receipt, and an
account with a subscription still running is refused (``active_subscription``).
"""
from __future__ import annotations

import os
from typing import Optional

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
#: Stripe subscription statuses under which the subscription can still charge the person.
LIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid", "incomplete"})
#: What the freeze removes from ``users.data``: every way a new meeting reaches this account
#: (calendar auto-join) or its data leaves it (the webhook). ``erasing`` marks the account so bot
#: spawn refuses it (``bot-context``).
FROZEN_DATA_KEYS = ("calendar_connections", "calendar_ics_url", "calendar_auto_join",
                    "webhook_url", "webhook_secret", "webhook_events")
ERASING_KEY = "erasing"


class MeetingsHopFailed(Exception):
    """meeting-api did not confirm the erase: ``status`` is the answer to give (409 a bot is still
    running, 502 otherwise), ``error`` and ``detail`` say why, ``running`` names the live bots."""

    def __init__(self, status: int, error: str, detail: str, running: Optional[list] = None):
        super().__init__(detail)
        self.status, self.error, self.detail, self.running = status, error, detail, running or []


def meeting_api_url() -> str:
    return (os.environ.get(MEETING_API_URL_ENV) or "").strip().rstrip("/")


def stripe_ids(data) -> dict:
    data = data if isinstance(data, dict) else {}
    return {key: data[key] for key in STRIPE_KEYS if data.get(key)}


def live_subscription(data) -> Optional[str]:
    """The status of a subscription that can still charge this person, or None."""
    data = data if isinstance(data, dict) else {}
    for key in ("subscription_status", "tx_subscription_status"):
        value = str(data.get(key) or "").strip().lower()
        if value in LIVE_SUBSCRIPTION_STATUSES:
            return value
    return None


def anything_deleted(receipt: dict) -> bool:
    """Whether meeting-api's receipt says it removed anything at all."""
    return any(isinstance(v, int) and v > 0 for v in (receipt.get("deleted") or {}).values()) or \
        bool((receipt.get("bot_accounts") or {}).get("deleted"))


async def erase_meetings(user_id: int) -> Optional[dict]:
    """meeting-api's receipt for erasing every meeting of ``user_id`` (``deleted``, ``retained``,
    ``bot_accounts``), or None when no meetings domain is configured. Raises ``MeetingsHopFailed``."""
    base = meeting_api_url()
    if not base:
        return None
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.delete(f"{base}/internal/users/{int(user_id)}/meetings",
                                    params={"wait_s": STOP_WAIT_S},
                                    headers={"X-Internal-Secret": internal_secret()})
    except httpx.HTTPError as exc:
        raise MeetingsHopFailed(502, "meetings_unreachable",
                                "meeting-api could not be reached to erase this person's meetings") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if r.status_code == 409:
        raise MeetingsHopFailed(409, "bot_still_running",
                                "A bot of this person is still in a meeting. Retry once it has left.",
                                running=body.get("running"))
    if r.status_code != 200 or not isinstance(body.get("deleted"), dict):
        raise MeetingsHopFailed(502, "meetings_not_confirmed",
                                f"meeting-api did not confirm the erase ({r.status_code})")
    return body
