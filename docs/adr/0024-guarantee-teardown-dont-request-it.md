# ADR 0024 — Guarantee teardown, don't request it (P22)

**Status:** accepted · 2026-06-23 · introduces **P22** · promotes LEARNINGS #50 (ORPH1)

## Context

A user could `POST /bots` and then **immediately `DELETE`** it and be left with an **orphan**: the DB
said `stopping`, but a real bot was live in the meeting, never told to leave. ("A symptom of a serious
design gap.")

The stop path only **PUBLISHED** a fire-and-forget `bot_commands:meeting:{id}` `{action:"leave"}` over
redis pub/sub and trusted the bot to self-leave. **Redis pub/sub has no buffering for late or booting
subscribers** — a message published while no one is subscribed is dropped, every time. A `DELETE`
arriving right after `POST` runs while the bot is still **booting** (status `requested`/`joining`/
`awaiting_admission`, not yet subscribed to its command channel), so the leave is lost — the bot then
joins and is never told to leave. There is a second, narrower window: the `DELETE` can land mid-spawn,
*before* the workload id has even been written back (`set_bot_container`), so a direct teardown has no
id to target yet.

The teardown **requested** an effect over an unreliable channel instead of **guaranteeing** it. This is
the half-promoted learning ADR-0017/0018 warns about: a surprise must be promoted **twice** — to the
architecture (principle + gate + ADR) *and* the learnings log — but #50 lived only in the log.

## Decision

Adopt **P22 — Guarantee teardown, don't request it.** A destructive lifecycle effect must be
**guaranteed at the boundary**, not delegated to a fire-and-forget message a not-yet-subscribed
consumer can miss. Concretely, the stop/teardown path **pairs** three moves:

- **Graceful command for a CONFIRMED-listening consumer.** An `active`/`needs_help` bot IS subscribed,
  so it still gets the published `leave` — it leaves gracefully and **finalizes its recording cleanly**.
  The graceful path is kept precisely because a hard kill would lose that clean finalize.
- **Hard guarantee when the consumer can't be confirmed listening.** A **booting** bot (status in
  `{requested, joining, awaiting_admission}`) has likely not subscribed yet, so the stop **directly
  kills the workload** via `runtime.delete_workload(bot_container_id)` — it has nothing to finalize, so
  there is no graceful state to preserve. The leave is still published (belt-and-suspenders), but the
  teardown no longer *depends* on it. (B1 — `lifecycle/stop_router.py`.)
- **Reconcile the create/destroy race.** The spawn re-checks for a stop that landed mid-boot
  (`get_status_by_session` → `stopping`/`completed`/`failed`) **after** the workload id is known, and
  tears the just-spawned workload down — closing the window where the `DELETE` arrived before
  `set_bot_container` so the stop's own direct teardown had no id to target. (C — `bot_spawn/service.py`.)

A reconcile loop (B2) is the standing backstop: it kills the workload of meetings stuck `stopping` whose
active bot somehow missed the leave.

**Gate it** under `gate:eval`'s `meeting-lifecycle` path: the orphan regressions
`test_stop_of_booting_bot_tears_down_workload_no_orphan` (B1) and
`test_spawn_reconciles_a_stop_that_raced_the_boot` (C) live in
`core/meetings/services/meeting-api/tests/test_robustness_seam.py`, run under `gate:python`, and are
banked as standing regressions — a regression that re-introduces the orphan turns the suite RED.

## Consequences

- The POST→immediate-DELETE orphan is closed and **L4-verified live on bbb** (POST then DELETE → no
  orphan container). The graceful leave is preserved for the only case where it adds value (a confirmed
  `active` bot finalizing its recording).
- Trade-off: the stop path now needs the `runtime` (`RuntimeClient`) port injected — a destructive
  capability on what was a publish-only route — and may kill a workload that *would* have self-left,
  trading a redundant kill for the orphan guarantee. The direct teardown is best-effort (logged, never
  fails the stop); the reconcile loop is the backstop if it ever does.
- **Generalizes:** fire-and-forget to a not-yet-subscribed consumer is a lost message. Any destructive
  effect delivered over an unbuffered channel (pub/sub) must be paired with a guarantee at the boundary
  and a reconcile for the create/destroy race — never trusted to a single best-effort message.
