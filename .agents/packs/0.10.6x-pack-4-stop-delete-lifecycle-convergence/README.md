# Pack 4 — Stop/Delete Lifecycle Convergence

This evidence dir documents what shipped on
`codex/pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence` (PR
[#365](https://github.com/Vexa-ai/vexa/pull/365), epic
[#359](https://github.com/Vexa-ai/vexa/issues/359)) and how it was
validated for the 0.10.6.3 stitched candidate.

## What shipped

Pack 4 converges all stop / delete paths onto a single honest lifecycle:

- **Internal callback secret preservation** — runtime-api now stores the
  callback headers in container state and replays them on exit
  callbacks; meeting-api internal endpoints reject calls without the
  shared secret. (`runtime-api/api.py`, `runtime-api/lifecycle.py`,
  `vexa-bot/core/src/services/unified-callback.ts`,
  `vexa-bot/core/src/types.ts`, `vexa-bot/core/src/index.ts`.)
- **Browser-session routing** — browser-session runtime metadata
  carries `connection_id: bs:<meeting_id>` so callbacks route directly
  to the meeting row.
- **Runtime-API explicit DELETE** — owns deletion, fires the stored
  callback, then removes primary runtime state so deleted browser
  sessions cannot resurface as stale stopped containers
  (`runtime-api/api.py`).
- **Stale-stopping sweep correctness** — sweeps now key off immutable
  creation bounds and status-transition timestamps, not the mutable
  `updated_at`, so they do not race meeting-api on legitimate
  in-progress stops (`meeting-api/sweeps.py`).
- **Orphan-window fix** — see below.

## Orphan-window fix (the headline)

**Symptom (pre-Pack 4):** `POST /bots` followed by an immediate
`DELETE` flipped the DB to `status=completed` / `failed` within ~500 ms
while the bot container kept running for another 5-15 s. State lied
about reality.

**Root cause:** two paths in the DELETE flow ran the classifier and
flipped the meeting straight to a terminal status synchronously while
only enqueueing the container stop in the background.

1. `meetings.py` fast-path for pre-active stops (REQUESTED / JOINING /
   AWAITING_ADMISSION, less than 5 s old) classified + flipped
   immediately.
2. `callbacks.py` `bot_status_change_callback` flipped
   STOPPING → terminal when the bot fired `graceful_leave`, which runs
   *inside* the bot before the container is torn down.

**Fix:** both paths now move the meeting to `STOPPING` (an honest
"still cleaning up" state) and persist their classifier verdict in
`meeting.data.bot_exit_classification`. The runtime-api `exit_callback`
handler — only fired *after* `docker rm` succeeds — reads that verdict
and performs the actual `STOPPING → target` transition.

Result on the orphan probe:

```
T+0s   db=stopping   container=running   <- truthful
T+~6s  db=completed  container=gone      <- truthful
```

## `bot_exit_classification` semantics

`meeting.data.bot_exit_classification` is the inter-path handoff that
defers the terminal transition. Shape:

```json
{
  "target_status": "completed" | "failed",
  "completion_reason": "stopped" | "stopped_before_admission" | ...,
  "bot_reported_reason": "<bot-reported reason or null>",
  "classified_at": "<ISO 8601>",
  "classified_by": "meetings.fast_path_stop" | "callbacks.bot_status_change" | ...
}
```

Stamped at the *moment of decision* (DELETE fast-path or bot
graceful_leave). Consumed by `exit_callback` after `docker rm`. The
exit_callback re-classifies via `_classify_stopped_exit` if the field
is missing or malformed, so absence is a graceful degradation, not a
crash.

## Idempotent stop

DELETE is safe to retry. Patterns covered:

- **Double DELETE** — scenario 12. The second DELETE no-ops cleanly
  because the meeting is already in `STOPPING` or terminal.
- **DELETE of non-existent meeting** — scenario 13. Returns a clean
  4xx; nothing in DB / Docker / Redis is mutated.
- **Concurrent DELETE on meeting + browser-session** — scenario 9.
  Both paths converge independently without interleaving.
- **Rapid start / stop loop** — scenario 14. Each iteration reaches
  terminal status with the container removed before the next iteration
  starts.

## Lifecycle convergence runner

`bin/lifecycle-convergence-runner.sh` is the autonomous evidence
producer for this pack. It exercises 9 stop/delete scenarios against
the live isolated Compose lane (`vexa_0-10-6x-pack-4-stop-delete-
lifecycle-convergence_compose`) and writes per-scenario verdicts plus
a top-level `summary.json`.

Latest run: `synthetic/lifecycle-convergence/summary.json` —
**9 passed, 0 failed.**

Scenarios:

| # | scenario | what it proves |
|---|----------|----------------|
| 03 | `no-joined-timeout` | timeout path converges to terminal + container reap |
| 05 | `force-kill` | runtime-api force-kill converges DB + Docker + Redis |
| 06 | `browser-session-delete` | explicit browser-session DELETE removes container within seconds |
| 09 | `concurrent-stops` | parallel DELETEs on meeting + browser-session converge independently |
| 10 | `stop-during-joining` | DELETE during JOINING reaches terminal (see *Known limitation* below) |
| 11 | `instant-start-stop` | start + immediate stop converges |
| 12 | `double-stop-idempotency` | second DELETE is a clean no-op |
| 13 | `stop-nonexistent` | DELETE of unknown meeting is a clean 4xx |
| 14 | `rapid-loop` | repeated start/stop reaches terminal each iteration |

Each scenario verdict combines DB status, Docker container presence,
Redis session-state key count, and meeting-api exit-callback counts —
no human eyeballing required.

## Known limitation — stop-during-`joining` does not stamp `bot_exit_classification`

Filed as [#383](https://github.com/Vexa-ai/vexa/issues/383). Tracked,
not fixed in Pack 4.

When `DELETE` arrives while the bot is still in `joining` state, the
bot does not currently fire the graceful self-leave path. The meeting
still converges — the orphan-window fix guarantees the DB never
reports `completed` before the container exits — but the convergence
is fulfilled by the outbox stale-stopping sweep (within ~60 s), not
by runtime-api's `exit_callback`. Because there is no `exit_callback`
and the joining fast-path takes the `stop_requested` short-circuit
(no classifier call), `meeting.data.bot_exit_classification` stays
`NULL` on this path.

Visible signature in scenario 10:

```json
{ "final_status": "failed", "callback_count": 0 }
```

This is a known gap, not a regression. Pack 4's invariant — DB never
lies about container liveness — still holds. The classification field
is best-effort metadata; the exit_callback re-classifies via
`_classify_stopped_exit` when it does fire, so consumers that want
the classification get it via the same path. Issue #383 owns the
follow-on to fire graceful self-leave during `joining` and surface
the classification on this path too.
