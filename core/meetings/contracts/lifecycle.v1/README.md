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
join). A `meeting_not_found` is NOT a stage attribution: it is the platform answering that the
meeting space does not exist (a dead, revoked or mistyped code), which is permanent by nature and
must never be retried (#1325). The machine-checked
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

No auth token (transport-layer), no tenancy fields (deferred). Goldens validated by `gate:schema`.
