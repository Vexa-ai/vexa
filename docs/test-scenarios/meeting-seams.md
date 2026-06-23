# Meeting seam scenario catalog

Behavior-named scenarios the 0.12 hardening campaign pins as repeatable tests. Each row names its probes
and the EXACT expected contract state. `status` ∈ 🟢 pinned (test green) · 🟡 in-flight · 🔴 open.
Add a row per known failure class; a row is "done" when every named probe is green.

```yaml
# ── Join / admission taxonomy (G1) ───────────────────────────────────────────
- id: admission-denial-is-permanent-not-retried
  status: green
  seam: "@vexa/join AdmissionError -> join-driver -> orchestrator -> lifecycle -> retry"
  module_probe: core/meetings/services/bot/src/join-driver.test.ts   # admissionOutcomeToJoinOutcome
  seam_probe: core/meetings/services/bot/src/orchestrator.test.ts    # outcome 'rejected' -> awaiting_admission_rejected
  expected:
    join_outcome: rejected
    failure_stage: awaiting_admission
    completion_reason: awaiting_admission_rejected
    retry_class: permanent          # lifecycle/retry.py: NOT re-spawned (was: thrown -> join_failure -> retried)
  note: >
    The admission wait THROWS a typed AdmissionError(outcome); the join-driver now maps the outcome
    instead of letting it fall through to the orchestrator's blanket transient join_failure.

- id: admission-lobby-timeout-is-transient
  status: green
  seam: "@vexa/join AdmissionError -> join-driver -> orchestrator -> lifecycle -> retry"
  module_probe: core/meetings/services/bot/src/join-driver.test.ts
  expected:
    join_outcome: timeout
    completion_reason: awaiting_admission_timeout
    retry_class: transient          # a waiting-room timeout is a legit retry

- id: gmeet-error-page-is-blocked-not-host-denial
  status: open                      # lane:contract + detection follow-up
  seam: "join detection -> bot-orchestrator -> lifecycle"
  module_probe: core/meetings/modules/join/src/googlemeet/admission.test.ts
  seam_probe: bot orchestrator fake join driver
  expected:
    failure_stage: awaiting_admission
    completion_reason: blocked       # NEEDS a new sealed CompletionReason value (lifecycle.v1, lane:contract)
  note: >
    checkForGoogleRejection conflates Google error/block pages with a host denial. Full fix needs a
    distinct `blocked` reason in the sealed lifecycle.v1 enum (human-gated re-seal). Until then a
    detected block surfaces as awaiting_admission_rejected (permanent — correct retry, imprecise reason).

# ── Remaining campaign seams (rows added as each lands) ───────────────────────
- id: recording-concurrent-finalize-no-jsonb-lost-update
  status: green
  seam: "upload_chunk / finalize_master -> repo.mutate_recordings (one FOR UPDATE row lock)"
  seam_probe: core/meetings/services/meeting-api/tests/test_recordings.py  # test_concurrent_chunk_uploads_do_not_lose_updates
  expected:
    two_concurrent_folds: { chunk_count: 3 }   # atomic read->modify->write; old get+put (separate txns) lost one -> 2
  note: >
    The fake proves the SERVICE uses the atomic primitive; the SQL adapter's SELECT … FOR UPDATE spanning
    read→commit is the real cross-process mechanism (L4 on live PG is the altitude-matched proof, per #49).
- id: recording-s3-ops-do-not-block-event-loop
  status: green
  seam: "S3Storage -> asyncio.to_thread"
  seam_probe: core/meetings/services/meeting-api/tests/test_recordings.py  # test_s3_storage_does_not_block_the_event_loop
  expected:
    heartbeat_during_blocking_upload: ticks_freely   # boto3 offloaded to a thread; the loop keeps serving
# - webhook-billing-exactly-once-per-meter-session          (G5)   status: open
- id: spawn-transcribe-enabled-string-false-is-false
  status: green
  seam: "POST /bots -> bot_spawn/router -> meeting.data"
  seam_probe: core/meetings/services/meeting-api/tests/test_api_agility.py  # test_post_bots_transcribe_enabled_string_false_is_false
  expected:
    request: { transcribe_enabled: "false" }
    persisted: { transcribe_enabled: false }   # _resolve_transcribe_enabled — no bare bool() coercion (was: "false" -> True)
- id: spawn-transcribe-requested-without-stt-fails-loud
  status: green
  seam: "POST /bots -> bot_spawn/router (precondition, before any DB write)"
  seam_probe: core/meetings/services/meeting-api/tests/test_api_agility.py  # test_post_bots_transcribe_without_stt_fails_loud + _no_transcription_spawns_without_stt
  expected:
    transcribe_true_no_stt: 503        # fail loud (P18) — never a silent deaf bot
    transcribe_false_no_stt: 201       # recording-only is legitimate; 503 fires ONLY when transcription is requested
- id: stop-active-bot-missed-leave-reconcile-kills-workload
  status: green
  seam: "stop -> stale `stopping` -> reconcile sweep -> runtime.delete_workload"
  seam_probe: core/meetings/services/meeting-api/tests/test_robustness_seam.py  # test_stop_reconcile_kills_orphan_workload (+ no-container-id, kill-best-effort)
  expected:
    db_row: completed                 # via the bot's own lifecycle callback (FSM/webhook/ws fire identically)
    workload: deleted                 # CC6/ADR-0024 — an active bot that missed the leave is killed, not left orphan
  note: completes ADR-0024 (guarantee teardown). L4: active bot whose leave is dropped → reconcile → no orphan container.
- id: runtime-workload-death-pre-join-drives-meeting-failed
  status: green
  seam: "runtime kernel -> POST /runtime/callback -> synthesize failed -> bot's own lifecycle callback"
  seam_probe: core/meetings/services/meeting-api/tests/test_robustness_seam.py  # test_runtime_callback_drives_failed_for_pre_active_dead_workload (+ active-noop, non-terminal/unknown-noop, fake roundtrip)
  expected:
    pre_active_terminal_workload: { status: failed, failure_stage: "<the pre-active stage>", completion_reason: join_failure }
    active_or_terminal_meeting: noop      # owned by the bot's own callback — don't race a legit completion
  note: >
    Scoped to PRE-ACTIVE (requested/joining/awaiting_admission). The active-crash case (a workload that
    dies mid-meeting) is a follow-up — a grace-windowed stale-active reconcile sweep, analogous to CC6.
# - dashboard_new-client-calls-are-served-or-stably-rejected (DF/contract)  status: open

# ── Deep integration cascades (L3-lite: the real app, multi-module, faked transports) ─────────────
- id: full-meeting-lifecycle-cascade
  status: green
  seam: "POST /bots -> lifecycle(joining→active→completed) -> {persist, ws fan-out, HMAC webhook}"
  cascade_probe: core/meetings/services/meeting-api/tests/test_lifecycle_cascade.py  # test_full_meeting_lifecycle_cascade
  expected:
    persist: status=completed (repo)
    ws: bm:{id}:status frames [joining, active, completed]
    webhook: 3 signed meeting.status_change deliveries to the per-user url (event-filter honored)
    envelope_log: 3 (one per real advance — no double-count)
- id: stop-cascade-tears-down-booting-bot-no-orphan
  status: green
  seam: "POST /bots -> DELETE /bots (booting) -> stop_router -> {leave published, runtime.delete_workload}"
  cascade_probe: core/meetings/services/meeting-api/tests/test_lifecycle_cascade.py  # test_stop_cascade_tears_down_a_booting_bot_no_orphan
  expected: { status: stopping, leave: published, workload: deleted }   # ORPH1/B1 end to end
```
