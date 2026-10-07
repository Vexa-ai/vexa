"""Fresh clock observations and identity-owned timezone preferences."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def clock_context(zone='', now=None):
    now = now or datetime.now(timezone.utc)
    result = {'utc_now': now.astimezone(timezone.utc).isoformat(timespec='seconds'),
              'timezone': zone or None, 'timezone_required': not bool(zone)}
    try:
        if zone:
            local = now.astimezone(ZoneInfo(zone))
            result.update(local_now=local.isoformat(timespec='seconds'), abbreviation=local.tzname())
    except (ZoneInfoNotFoundError, ValueError):
        result.update(timezone=None, timezone_required=True)
    result['instruction'] = ('Ask the user which timezone to remember, then call timezone_set. '
        'Report UTC explicitly until they answer.' if result['timezone_required'] else
        'Use local_now for this user. Call current_time again for later time-sensitive answers; do not reuse chat timestamps.')
    return result


def register(runtime):
    def observe():
        status, data = runtime._http('GET', f'{runtime.ADMIN_API}/internal/users/{runtime.me()}/settings', runtime._internal_headers())
        result = clock_context(data.get('timezone','') if status == 200 and isinstance(data,dict) else '')
        if status != 200:
            result.update(preference_status='unavailable', instruction='Timezone preferences could not be read. Do not claim they are missing. Report UTC explicitly and retry later.')
        return result

    @runtime.mcp.tool()
    @runtime._anon_guard
    def current_time() -> str:
        """Read the actual current UTC and user-local time. Call for 'now', today, relative dates and scheduling; never infer the clock from chat history. If timezone_required, ask their timezone and persist it with timezone_set."""
        return json.dumps(observe())

    @runtime.mcp.tool()
    @runtime._anon_guard
    def timezone_set(timezone: str) -> str:
        """Remember this person's explicitly stated IANA timezone (e.g. Europe/Lisbon) across chats. Ask when unknown; do not guess from server location or an email address."""
        scope = runtime.CALL_SCOPE.get()
        if scope is not None and scope.get('regime') != 'human':
            return json.dumps({'status':'refused','reason':'human_session_required'})
        try: ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return json.dumps({'status':'invalid_timezone','instruction':'Ask for an IANA timezone such as Europe/Lisbon.'})
        settings, refused = runtime._settings_set(runtime.me(), 'timezone', timezone)
        if refused:return json.dumps({'status':'unavailable','instruction':'Timezone was not saved. Try again later.'})
        return json.dumps({'status':'saved', **clock_context(settings.get('timezone',''))})

    original = runtime.whats_waiting
    runtime.mcp.remove_tool('whats_waiting')
    @runtime.mcp.tool()
    @runtime._anon_guard
    def whats_waiting() -> str:
        """Read pending work and a fresh clock with the user's remembered timezone. Ask and save timezone if unknown. For time alone, use current_time."""
        result = json.loads(original())
        if isinstance(result,dict):result['time_context']=observe()
        else:result={'pending':result,'time_context':observe()}
        return json.dumps(result)
