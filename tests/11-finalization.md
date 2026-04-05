---
id: test/verify-finalization
type: validation
requires: [test/verify-transcription]
produces: [FINALIZATION_OK]
validates: [bot-lifecycle]
docs: [features/bot-lifecycle/README.md]
mode: machine
---

# Verify Finalization

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Stop all bots for a meeting and verify clean shutdown. Validates the second half of bot lifecycle: `active → stopping → completed`.

## Inputs

| Name | From | Description |
|------|------|-------------|
| GATEWAY_URL | test/infra-up | API gateway URL |
| MEETING_PLATFORM | test/create-live-meeting | Platform |
| NATIVE_MEETING_ID | test/create-live-meeting | Meeting ID |
| TOKENS | test/api-full + speaker tokens | All API tokens that have bots in this meeting |

## Script

```bash
eval $(./11-finalization.sh GATEWAY_URL MEETING_PLATFORM NATIVE_MEETING_ID TOKEN1 TOKEN2 TOKEN3)
```

## Steps

1. DELETE /bots/{platform}/{native_id} for each token
2. Wait for terminal state
3. For each bot: assert status=completed, end_time set, completion_reason=stopped
4. Assert transition chain ends with `active → stopping → completed`

## Outputs

| Name | Description |
|------|-------------|
| FINALIZATION_OK | true if all bots completed cleanly |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Bot status=failed after stop | Container crashed during recording upload | Check shm_size, check MinIO connectivity | |
| completion_reason empty | Container killed (OOM/SIGKILL) instead of clean exit | Check container exit code, memory limits | |
| Bot stays active after DELETE | Stop command not reaching container | Check Redis PUBLISH, check bot_commands channel | |

## Docs ownership

After this test runs, verify and update:

- **features/bot-lifecycle/README.md**
  - DoD table: update Status, Evidence, Last checked for item #3 (DELETE /bots stops bot, reaches completed) — this test owns the `active -> stopping -> completed` tail of the lifecycle
  - States table: verify the `stopping -> completed` transition produces `completion_reason=stopped` and `end_time` is set as documented
  - Note "11-finalization owns `active -> stopping -> completed`": verify this matches the actual transition chain observed in `data.status_transition` for each bot stopped
