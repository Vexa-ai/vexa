# Review Notes

Pack 4 is implemented on branch `codex/pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`.

## Code changes

- Runtime API now preserves callback headers in container state and sends them on exit callbacks.
- Meeting API internal callback and scheduler timeout paths now require the internal secret.
- Browser-session runtime metadata now includes `connection_id: bs:<meeting_id>`, letting callbacks route directly to the meeting.
- Browser-session and normal bot launch payloads carry `internalSecret` / `INTERNAL_API_SECRET` where needed.
- Stale-stopping sweeps now use immutable creation bounds and status-transition timestamps instead of mutable `updated_at`.
- Explicit Runtime API delete marks delete ownership, fires the stored callback, and removes primary runtime state after callback queuing/delivery, so deleted browser sessions no longer remain queryable as stale stopped containers.

## Validation

- Synthetic tests: pass.
- Compose stop/delete convergence: pass after rebuilt branch `meeting-api` and `runtime-api` images.
- Hardenloop: no release blockers, incomplete scanner coverage due missing local tools.
- Lite: blocked by official fixed-name/fixed-port Lite workflow on a host that already has default Lite lanes running.

## Residual risks

- Full `services/vexa-bot/core` build remains blocked by pre-existing TypeScript issues unrelated to Pack 4 callback/header changes.
- Lite needs a future isolated validation path or official support for non-default names/ports before this pack can truthfully claim a Lite gate pass on this host.
