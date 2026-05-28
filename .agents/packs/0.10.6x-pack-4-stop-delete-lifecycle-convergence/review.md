# Review Notes

Pack 4 is implemented on branch `codex/pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`.

## Code changes

- Runtime API now preserves callback headers in container state and sends them on exit callbacks.
- Meeting API internal callback and scheduler timeout paths now require the internal secret.
- Browser-session runtime metadata now includes `connection_id: bs:<meeting_id>`, letting callbacks route directly to the meeting.
- Browser-session and normal bot launch payloads carry `internalSecret` / `INTERNAL_API_SECRET` where needed.
- Stale-stopping sweeps now use immutable creation bounds and status-transition timestamps instead of mutable `updated_at`.
- Explicit Runtime API delete marks delete ownership, fires the stored callback, and removes primary runtime state after callback queuing/delivery, so deleted browser sessions no longer remain queryable as stale stopped containers.
- **Orphan-window fix**: deferred STOPPING → terminal transition until runtime-api's `exit_callback` fires (i.e. after `docker rm` succeeds). Both pre-Pack-4 synchronous-flip paths (meetings.py fast-path, callbacks.py bot graceful-leave) now persist their classifier verdict in `meeting.data.bot_exit_classification` and let the exit_callback perform the terminal transition. See `README.md` and the `synthetic/lifecycle-convergence/` evidence for details and per-scenario verdicts.

## Validation

- Synthetic tests: pass.
- Compose stop/delete convergence: pass after rebuilt branch `meeting-api` and `runtime-api` images.
- Hardenloop: no release blockers, incomplete scanner coverage due missing local tools.
- Lite: blocked by official fixed-name/fixed-port Lite workflow on a host that already has default Lite lanes running.

## Residual risks

- Full `services/vexa-bot/core` build remains blocked by pre-existing TypeScript issues unrelated to Pack 4 callback/header changes.
- Lite needs a future isolated validation path or official support for non-default names/ports before this pack can truthfully claim a Lite gate pass on this host.

## Known limitation — stop-during-`joining`

Tracked as [#383](https://github.com/Vexa-ai/vexa/issues/383). When
`DELETE` arrives during the bot's `joining` state, the bot does not
fire the graceful self-leave path. Container stop is fulfilled by
the outbox stale-stopping sweep within ~60 s, and Pack 4's
orphan-window fix still ensures the DB never reports `completed`
before the container exits — but `meeting.data.bot_exit_classification`
is not stamped on this path (the joining fast-path short-circuits on
`stop_requested` without calling the classifier, and no
`exit_callback` ever fires because runtime-api is not the agent that
removes the container on this path). Convergence still happens; the
classification metadata is best-effort and the exit_callback handler
re-classifies via `_classify_stopped_exit` whenever it does fire.
This is a known gap, not a regression against pre-Pack-4 behaviour.
Filed as #383 for follow-on. Visible signature in
`synthetic/lifecycle-convergence/scenario-10-stop-during-joining/`:
`final_status=failed, callback_count=0`.
