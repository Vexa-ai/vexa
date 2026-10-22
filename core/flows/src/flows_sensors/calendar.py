"""The calendar sensor kind — a person's Google Calendar, incrementally, through the credential broker.

Read-only and token-free on this side: the pool hands the sensor's cursor (a sync token) to the
broker's `sensors` role (`POST /api/internal/sensor/calendar-changes`, credential-broker.v1), signed
for exactly the person who owns the sensor; the broker refreshes the token if it must, reads the
changes and answers normalised events and the next cursor. The broker reads only under a live sensor
grant that person issued (`calendar_sensor_grant`, through their own signed identity): without one
the read is refused with `sensor_grant_required`, and the sensor stops with `GRANT_NEEDED` as its
reason until the person grants it and enables the sensor again. Each change becomes
`calendar.event.created|updated|cancelled` with refs `event_id`, `start`, `end`, `title`, `video_url`,
`self_response`, `organizer_self`, `status`, `etag`; the dedupe key is `event_id:etag`, so a resync
re-reads without re-emitting. Whether an event was seen before (created vs updated) is not known to a
stateless read, so a change that is not a cancellation is `updated` unless the event is new to the
cursor's window — `created` on a first read. Triggers listen on `calendar.event.*` and need neither.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

import flows_config

from . import broker_assertion
from .base import Change, SensorFault, SensorKind, SensorRow, Tick

PATH = "/api/internal/sensor/calendar-changes"
TIMEOUT_S = 20.0
#: The broker's reason for a read with no live grant from the sensor's person (credential-broker.v1).
GRANT_REQUIRED = "sensor_grant_required"
#: What the person and their agent read on the sensor: the one step that lets it read again.
GRANT_NEEDED = ("this sensor may not read your calendar: let it with calendar_sensor_grant in chat, "
                "then test and enable it again")


def broker_call(owner: str, path: str, body: dict, *, session: str) -> dict:
    """One signed POST to the broker as the `sensors` role, for `owner`. Raises SensorFault."""
    base = (flows_config.get("VEXA_FLOWS_SENSORS_BROKER_URL") or "").rstrip("/")
    key_file = flows_config.get("VEXA_FLOWS_SENSORS_KEY_FILE") or ""
    if not base or not key_file:
        raise SensorFault("sensors are not configured on this deployment (no broker)", retryable=False)
    try:
        key = broker_assertion.load_key(key_file)
    except broker_assertion.KeyUnavailable:
        raise SensorFault("the sensors key is not readable", retryable=False) from None
    raw = json.dumps(body, separators=(",", ":")).encode()
    header = broker_assertion.sign(key, role="sensors", actor=owner, session=session, method="POST",
                                   path=path, body=raw)
    req = urllib.request.Request(base + path, data=raw, method="POST", headers={
        broker_assertion.HEADER: header, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — the broker's fixed URL
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read() or b"{}")
        except ValueError:
            answer = {}
        detail = answer.get("detail") if isinstance(answer, dict) else None
        if e.code == 409 and isinstance(answer, dict) and answer.get("reason") == GRANT_REQUIRED:
            raise SensorFault(GRANT_NEEDED + (f" ({str(detail)[:120]})" if detail else ""),
                              retryable=False) from None
        if e.code == 409:
            raise SensorFault("no ready calendar connection — connect Google Calendar first",
                              retryable=False) from None
        if e.code in (401, 403):
            raise SensorFault(f"the broker refused the read ({e.code})", retryable=False) from None
        raise SensorFault(f"calendar read failed ({e.code}{': ' + str(detail)[:120] if detail else ''})") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SensorFault("the broker could not be reached") from None


class CalendarSensor(SensorKind):
    kind = "calendar"
    event_types = ("calendar.event.created", "calendar.event.updated", "calendar.event.cancelled")
    min_interval_s = 60.0
    connection = "google_calendar"

    def __init__(self, call=None):
        self._call = call or broker_call

    def tick(self, sensor: SensorRow, cursor: Optional[str]) -> Tick:
        if not sensor.uid:
            raise SensorFault("a calendar sensor belongs to one person", retryable=False)
        body = {"owner": sensor.uid, "cursor": cursor,
                **({"connection_id": sensor.connection_id} if sensor.connection_id else {})}
        answer = self._call(sensor.uid, PATH, body, session=f"sensor:{sensor.sensor_id}")
        if answer.get("resync_required"):
            return Tick(changes=[], cursor=None, resync=True)
        first = cursor is None
        changes = []
        for item in answer.get("items") or []:
            if not isinstance(item, dict) or not item.get("event_id"):
                continue
            kind = ("cancelled" if item.get("status") == "cancelled"
                    else "created" if first else "updated")
            changes.append(Change(f"calendar.event.{kind}",
                                  f"{item['event_id']}:{item.get('etag') or item.get('updated') or ''}",
                                  dict(item)))
        return Tick(changes=changes, cursor=answer.get("cursor"))


#: Every kind this deployment can run, by name. Other packs register theirs (`register`).
KINDS: dict = {"calendar": CalendarSensor()}


def register(kind: SensorKind) -> None:
    """A sensor kind another pack provides (e.g. the shared mailbox's invite sensor)."""
    KINDS[kind.kind] = kind


# The shared mailbox's sensor kind (`mailbox.py`) — same pool, same tools, owned by whoever creates it.
from .mailbox import MailboxSensor as _MailboxSensor  # noqa: E402

register(_MailboxSensor())
