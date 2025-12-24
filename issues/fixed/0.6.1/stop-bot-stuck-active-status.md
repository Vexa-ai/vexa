# Stop Bot reports "processed" but meeting remains Active

- **Status**: Needs Investigation
- **Area**: `bot-manager` service, meeting status lifecycle, Redis command routing, container stop fallback
- **Severity**: Medium (users see incorrect meeting status; stop requests sometimes ineffective)

## Summary

Users report that calling Stop Bot returns 202 "is being processed", but the meeting status stays "active" indefinitely. In most cases Stop Bot works, but an edge case leads to incorrect/unchanged status and a seemingly active meeting after attempting to stop.

This appears related to the designed flow relying on the bot's exit callback to finalize the DB status. When that callback does not occur (or is routed incorrectly), the DB never transitions to a terminal state.

## Expected vs Actual

- **Expected**:
  - Stop request sends a leave command to the specific running bot session.
  - Bot leaves and calls exit callback → DB transitions to `completed` (or `failed`).
  - If bot does not respond, delayed container stop ensures cleanup and DB finalization happens.

- **Actual (edge case)**:
  - Stop request returns 202 and publishes `stopping` on Redis, but DB status remains `active`.
  - After delayed container stop, the exit callback does not update the DB, leaving the meeting stuck.

## Relevant Code Paths

Stop endpoint sends leave command, schedules delayed stop, publishes `stopping` via Redis, but intentionally does not change DB status:

```684:827:vexa/services/bot-manager/app/main.py
@app.delete("/bots/{platform}/{native_meeting_id}",
             status_code=status.HTTP_202_ACCEPTED,
             summary="Request stop for a bot",
             description="Stops a bot from any status (requested, joining, awaiting_admission, active). Sends a 'leave' command to the bot via Redis and schedules a delayed container stop. Returns 202 Accepted immediately.",
             dependencies=[Depends(get_user_and_token)])
async def stop_bot(
    platform: Platform,
    native_meeting_id: str,
    background_tasks: BackgroundTasks, # Keep BackgroundTasks
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    ...
    # Publish 'leave' command via Redis Pub/Sub
    ...
    # 4. Schedule delayed container stop task
    ...
    # 5. Update Meeting status (Consider 'stopping' or keep 'active')
    # Note: We don't have a 'stopping' status in the new system
    # The bot will transition directly to 'completed' or 'failed' via callback
    ...
    # 5.1. Publish meeting status change via Redis Pub/Sub
    await publish_meeting_status_change(meeting.id, 'stopping', redis_client, platform_value, native_meeting_id, meeting.user_id)
    ...
```

Exit callback updates the DB to completed/failed when called by the bot:

```858:989:vexa/services/bot-manager/app/main.py
@app.post("/bots/internal/callback/exited",
          status_code=status.HTTP_200_OK,
          summary="Callback for vexa-bot to report its exit status",
          include_in_schema=False)
async def bot_exit_callback(...):
    ...
    if exit_code == 0:
        success = await update_meeting_status(meeting, MeetingStatus.COMPLETED, db, ...)
    else:
        success = await update_meeting_status(meeting, MeetingStatus.FAILED, db, ...)
    ...
```

Delayed stop kills the container after 30s, but does not update meeting status (assumes exit callback will handle it):

```286:299:vexa/services/bot-manager/app/main.py
async def _delayed_container_stop(container_id: str, delay_seconds: int = 30):
    await asyncio.sleep(delay_seconds)
    await asyncio.to_thread(stop_bot_container, container_id)
```

Meeting status definitions include `STOPPING` as a valid intermediate state (but stop endpoint currently does not persist it):

```30:56:vexa/libs/shared-models/shared_models/schemas.py
class MeetingStatus(str, Enum):
    REQUESTED = "requested"
    JOINING = "joining"
    AWAITING_ADMISSION = "awaiting_admission"
    ACTIVE = "active"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
```

## Reproduction (intermittent)

Using the test notebook `vexa/nbs/0_basic_test.ipynb`:
- Request a bot
- Admit and verify active status
- Call `client.stop_bot(platform=..., native_meeting_id=...)`
- Observe some meetings remain `active` in DB long after the call, despite the 202 response and `stopping` pub/sub event

## Hypotheses

- **H1: Exit callback never arrives after delayed force stop**
  - If the bot is killed by the delayed stop before calling `/bots/internal/callback/exited`, the DB is never updated, leaving meeting `active`.

- **H2: Command routing to the wrong session UID**
  - In `stop_bot` the leave command is published to the "earliest" session UID (ascending by `session_start_time`). If the bot restarted and the current session UID differs from the earliest one, the live bot never receives the command.
  - Contrast: `update_bot_config` resolves the current session via Redis `current_uid` mapping (or latest session) before publishing.

- **H3: Redis/Network issues**
  - Transient Redis pub/sub delivery failure or network flakes could cause the bot to miss the leave command; if the bot also fails to self-exit, the fallback kill doesn’t update DB.

- **H4: Misleading status messaging vs DB**
  - Stop endpoint publishes `stopping` over Redis for real-time UI, but the DB intentionally remains `active` pending callback. If callback is missed, the UI and DB become inconsistent.

## Data to Collect

- For affected meetings:
  - `Meeting.id`, `status`, `bot_container_id`, `created_at`, `updated_at`, `data.stop_requested`
  - `MeetingSession` rows and their `session_uid` ordering
  - Redis key `bm:meeting:{platform}:{native_meeting_id}:current_uid` value at stop time
  - Bot logs around stop time: whether `leave` was received, whether callback was attempted
  - Bot-manager logs around stop time for publish success/failure and delayed stop execution
  - Container/Nomad/Docker events for the allocation/container lifecycle (exit code, kill signal)

## Immediate Workarounds (operational)

- If a meeting is stuck `active` but no container is running, manually set meeting to `completed` with reason `stopped` and run post-meeting tasks.
- Re-issue stop request; if Redis mapping is missing, stopping soon after start may hit the pre-active fast path (immediate finalize).

## Potential Fix Directions (to be validated)

- Align session UID resolution in `stop_bot` with `update_bot_config`:
  - Prefer Redis `current_uid`, fallback to latest `MeetingSession` instead of earliest.
- Add a safety finalizer when delayed stop runs:
  - After force-kill, verify container state and finalize DB (`completed` with reason `stopped`) if no further callbacks arrive within a short grace period.
- Add monitoring to detect orphaned meetings:
  - Periodic task compares DB `active` meetings vs actual running containers; auto-finalize or alert when mismatched.
- Optionally persist `STOPPING` in DB on stop request to reduce confusion and enable timeouts on the `stopping` state.

## Open Questions

1. How frequently do we observe missing exit callbacks after delayed stop?
2. Do we see multiple `MeetingSession` entries for affected meetings (bot restarts)?
3. Is Redis `current_uid` available and accurate at stop time in these incidents?
4. Any platform-specific correlation (Meet vs Teams)?

## Acceptance Criteria (for resolution)

- Stop Bot reliably transitions meetings from `active` to terminal (`completed` or `failed`), even if:
  - The bot does not process the leave command
  - The bot is force-killed by the delayed stop
  - The exit callback is missed
- No meetings remain `active` in DB when their container/allocation is no longer running.

---

### References

- `vexa/services/bot-manager/app/main.py` Stop, Exit Callback, Delayed Stop implementations
- `vexa/libs/shared-models/shared_models/schemas.py` `MeetingStatus`
- Test flows in `vexa/nbs/0_basic_test.ipynb`


