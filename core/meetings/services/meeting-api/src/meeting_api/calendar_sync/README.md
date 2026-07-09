# calendar_sync — ICS feed → planned meetings

One concern: turn a user's secret ICS calendar feed into PLANNED meeting rows (intent status
`scheduled`, `data.calendar_uid` provenance) so the Meetings surface shows what's coming and the
auto-join sweep sends the bot when each meeting starts. No OAuth — the user pastes the
secret-address ICS URL Google Calendar / Outlook already provide (`PUT /user/calendar`, identity
domain).

## Public surface
- `parse_ics(text, now, horizon_days=14)` → `{"events": [PlannedEvent], "cancelled_uids": […]}`
  (pure). **One event per UID — the next upcoming occurrence only** (a recurring meeting reuses one
  link; two active rows on one native id would violate `uq_meeting_active_user_platform_native`).
  Only events with a recognizable Meet/Zoom/Teams link import (`collector/meeting_link.py`).
- `sync_user(store, user_id, parsed, auto_join_default)` → upserts through the SAME planned-meeting
  store primitives `POST /meetings` uses (advisory-locked). Intent rows follow the feed; FSM rows
  are never touched; a manual plan on the same link is ADOPTED (uid stamped), not duplicated;
  vanished/cancelled UIDs retire their still-planned rows.
- `fetch_ics(url)` — **SSRF-pinned** (`webhooks/ssrf.build_pinned_transport`), 2 MB cap, no
  redirects. `fetch_configs(admin_api_url, secret)` — the internal discovery hop.

## Wiring (entrypoint)
`meeting_api.__main__._attach_background_loops` runs one sweep per `CALENDAR_SYNC_INTERVAL_S`
(default 300 s): configs → per-user fetch+parse+sync (per-user try/except — one bad feed never
stalls the sweep) → WS frames per changed row → `cal:sync:{user_id}` redis stamp
(`last_sync`/`last_error`/counts, read back by the terminal's calendar popover). Unset
`ADMIN_API_URL`/`INTERNAL_API_SECRET` → the loop no-ops (capability degrade, not boot-fail).

## Dependencies
`icalendar` + `python-dateutil` (both FINOS Category A), imported lazily. Depends on
`collector` (meeting_link + the planned-meeting store port) and `webhooks.ssrf` only.
