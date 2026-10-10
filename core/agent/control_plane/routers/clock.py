"""The person's clock: the actual current time in their remembered timezone, and the door that
remembers it.

Two routes the assembled MCP serves as `current_time` and `timezone_set`. The timezone is a fact
about the PERSON, so identity owns it (`/internal/users/{id}/settings`, the internal tier agent-api
already presents); this router reads and writes it there and never keeps a copy of its own.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone as _tz
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("agent_api.clock")

_TIMEOUT_S = 5


class TimezoneBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezone: str = Field(min_length=1, max_length=64,
                          description='an IANA timezone the person stated, e.g. Europe/Lisbon')


def clock_context(zone: str = '', now: "datetime | None" = None) -> dict:
    """The observed clock — UTC always, and local time when the zone is known and real."""
    now = now or datetime.now(_tz.utc)
    result = {'utc_now': now.astimezone(_tz.utc).isoformat(timespec='seconds'),
              'timezone': zone or None, 'timezone_required': not bool(zone)}
    try:
        if zone:
            local = now.astimezone(ZoneInfo(zone))
            result.update(local_now=local.isoformat(timespec='seconds'), abbreviation=local.tzname())
    except (ZoneInfoNotFoundError, ValueError):
        result.update(timezone=None, timezone_required=True)
    result['instruction'] = (
        'Ask the user which timezone to remember, then call timezone_set. '
        'Report UTC explicitly until they answer.' if result['timezone_required'] else
        'Use local_now for this user. Call current_time again for later time-sensitive answers; '
        'do not reuse chat timestamps.')
    return result


def build(*, subject_of, settings=None, **_) -> APIRouter:
    router = APIRouter()

    def _identity(method: str, subject: str, body: "dict | None" = None) -> "tuple[int, dict]":
        base = ((settings.admin_api_url if settings is not None else '') or '').rstrip('/')
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ''
        if not base or not secret:
            return 0, {}
        req = urllib.request.Request(
            f"{base}/internal/users/{urllib.parse.quote(str(subject), safe='')}/settings",
            method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Internal-Secret': secret, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:  # noqa: S310 — internal door
                return r.status, json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or '{}')
            except ValueError:
                return e.code, {}
        except (OSError, ValueError) as e:
            logger.warning("identity settings %s failed for subject=%s: %s", method, subject,
                           type(e).__name__)
            return 0, {}

    @router.get('/api/time')
    def current_time(request: Request):
        """Read the actual current UTC and user-local time. Call for 'now', today, relative dates and
        scheduling; never infer the clock from chat history. If timezone_required, ask their timezone
        and persist it with timezone_set."""
        status, data = _identity('GET', subject_of(request))
        result = clock_context(data.get('timezone', '') if status == 200 and isinstance(data, dict) else '')
        if status != 200:
            result.update(preference_status='unavailable',
                          instruction='Timezone preferences could not be read. Do not claim they are '
                                      'missing. Report UTC explicitly and retry later.')
        return result

    @router.put('/api/time/zone')
    def timezone_set(request: Request, body: TimezoneBody):
        """Remember this person's explicitly stated IANA timezone (e.g. Europe/Lisbon) across chats.
        Ask when unknown; do not guess from server location or an email address."""
        # A person in the loop only: `person` in routes.v1.json, refused by the app's one gate.
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return {'status': 'invalid_timezone',
                    'instruction': 'Ask for an IANA timezone such as Europe/Lisbon.'}
        status, data = _identity('PUT', subject_of(request), {'timezone': body.timezone})
        if status == 422:
            return {'status': 'invalid_timezone',
                    'instruction': 'Ask for an IANA timezone such as Europe/Lisbon.'}
        if status != 200:
            raise HTTPException(503, {'status': 'unavailable',
                                      'instruction': 'Timezone was not saved. Try again later.'})
        return {'status': 'saved', **clock_context(data.get('timezone', '') if isinstance(data, dict) else '')}

    return router
