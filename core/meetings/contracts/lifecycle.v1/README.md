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
`LifecycleEvent` (`$defs`): `connection_id` + `status` always; state-dependent `reason · exit_code ·
completion_reason · failure_stage · bot_logs · bot_resources · speaker_events` (terminal forensics).

No auth token (transport-layer), no tenancy fields (deferred). Goldens validated by `gate:schema`.
