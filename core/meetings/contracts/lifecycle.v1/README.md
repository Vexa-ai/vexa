# lifecycle.v1 — the bot's domain status

Distinct from `runtime.v1` (container lifecycle): this is the **bot's own status**, emitted to its
control-plane callback. The kernel never interprets it (ADR-0001: separate channels).

## States & legal transitions (the machine)
```
joining            → awaiting_admission · active · failed
awaiting_admission → active · needs_help · failed
needs_help   → active · failed
active             → completed · failed
completed          ∅   (terminal)
failed             ∅   (terminal)
```
`completed` carries a `completion_reason`; `failed` carries a `failure_stage`. Pre-active teardown
attribution (the control plane's reconcile path, when the workload dies before the bot reports
`active`): `awaiting_admission` → `awaiting_admission_timeout` (reaped while waiting in the lobby —
the room never admitted the bot), `requested`/`joining` → `join_failure` (died before it could
join). The machine-checked
`canTransition` lives in the **runtime/bot implementation** (Stage 2) — the contract documents it; the
impl enforces it (lean: no separate harness, B8).

## Shape
`LifecycleEvent` (`$defs`): `connection_id` + `status` always; the shipping producer also stamps
`timestamp`, the UTC time it observed the transition, before transport retries. That producer time
is the lifecycle fact used for admitted-to-departed runtime; callback receipt time is never a
service-duration clock. State-dependent fields are `reason · exit_code · completion_reason ·
failure_stage · bot_logs · bot_resources · speaker_events` (terminal forensics).

At the terminal boundary, meeting-api freezes a privacy-safe `service_provenance` projection:
admission/departure times, bot outcome, transcription provider (`vexa · customer · none`),
transcription outcome, and contract version. Provider ownership is selected when the bot is
created; it is never reconstructed later from a URL or current user setting. Endpoint URLs,
credentials, and tokens never enter the projection. A legacy meeting without frozen provider
ownership remains unresolved for downstream billing. An admitted mixed-version session whose
lifecycle event lacks a producer timestamp is likewise unresolved: receiver time remains useful
for operations, but retry latency makes it invalid for rating.

## `join_evidence` — typed join-failure evidence (#1059, #1058)

An **additive** field on a terminal `failed` event, riding this contract's liberal ingestion
(`additionalProperties: true`) exactly as `infra_fault` and `stt_fault` do — **no schema change, no
seal bump**. The sealed `CompletionReason` enum is deliberately untouched: it is the retry
classifier's input (`lifecycle/retry.py`), and this is a diagnostic axis, not a retry input.

It exists because `completion_reason` alone could not answer either question an operator asks of a
failed join. In hosted production, every one of 83 consecutive `join_failure` meetings carried
`data.reason = None`, and a ~20s Teams refusal shared its label with a ~13min Google Meet lobby
expiry — two different owners, two different fixes, one number.

```jsonc
"join_evidence": {
  "reason":      "awaiting_admission_timeout",
                                        // WHAT: awaiting_admission_rejected · awaiting_admission_timeout ·
                                        //   auth_session_missing · never_reached_lobby ·
                                        //   navigation_failure · stopped_while_joining · unknown
                                        //   (the first three are the SEALED enum's own words: one
                                        //    word per fact, so the axes layer instead of competing)
  "attribution": "host_action",         // WHO:  system_fault · user_action · host_action ·
                                        //   exogenous_platform · unknown
  "source":      "bot",                 // bot (first-hand) · reconcile · runtime_destroy · derived
  "stage":       "awaiting_admission",
  "detail":      "…the platform's own signal that triggered the classification (capped)…",
  "timings":     { "time_to_lobby_ms": 7000, "time_in_lobby_ms": 780000, "total_ms": 787000 },
  "lobby_budget_ms": 600000             // the deadline the control plane itself issued
}
```

The producer (`services/bot/src/join-evidence.ts`) classifies at the source, where the platform
signal and the clock actually are; meeting-api (`lifecycle/join_evidence.py`) validates that verdict
and **derives** one when a producer sends none, so reconcile-driven and runtime-destroy terminals —
and any bot too old to classify — are evidenced too. Persisted at `meeting.data.join_evidence`,
alongside a top-level `meeting.data.reason`.

`attribution` is the axis the reliability gate reads:
`system_failure_rate = failures(attribution = system_fault) / fair-chance meetings`, per release and
per platform. An absent timing is **absent**, never zero — "nobody measured" and "zero ms" are
different facts. Producing this evidence is **fail-open**: it describes a run that has already
ended, and no fault in it may alter the terminal being recorded.

No auth token (transport-layer), no tenancy fields (deferred). Goldens validated by `gate:schema`.
