# Groom — 260418-webhooks

| field        | value                                |
|--------------|--------------------------------------|
| release_id   | `260418-webhooks`                    |
| stage        | `groom`                              |
| entered_at   | `2026-04-18T20:12:28Z`               |
| actor        | `AI:groom`                           |
| predecessor  | `idle` (last release `260417-webhooks-dbpool` torn down in `ad535ef`) |

---

## Signal sources

| source | status | notes |
|--------|--------|-------|
| GitHub issues | **69 open** (fetched via `gh issue list --state open`, 2026-04-18) | 9 labelled `area: API / Webhooks / MCP`, of which **2 are bugs** (both MCP-scoped, not webhook-delivery); the rest are feature requests. No open GH issue reports the symptom the user is raising. |
| Discord reports | **SKIPPED** | Fetcher not yet migrated into repo (per README §4.2 — planned migration). External path at `/home/dima/dev/0_old/...` intentionally not used. This is a known groom-stage gap; noted so future cycles close it. |
| Internal notes — prior release | Reviewed | `tests3/reports/release-0.10.0-260417-1454.md` (the only artifact surviving the `ad535ef` teardown of `260417-webhooks-dbpool`). Scope.yaml was deleted with the release folder. |
| Human (user, this session) | **Primary** | *"webhooks for statuses other than finished not being delivered"* — the steering signal for this cycle. |

---

## Candidate packs

### Pack A — Status webhooks for non-`meeting.completed` events not delivered

- **source**: `human` (user, 2026-04-18)
- **owner feature**: `webhooks`
- **symptom**: user has a webhook URL configured with `webhook_events` including `meeting.started`, `meeting.status_change`, `bot.failed` (in addition to the default-enabled `meeting.completed`). Only `meeting.completed` is observed at the receiver; the other event types never fire, even across normal bot lifecycles.
- **prior claim**: release `260417-webhooks-dbpool` (green-gated on 2026-04-18T12:52:13Z) declared two DoDs that cover this:
  - `events-meeting-completed` (weight 10) — verified by `webhooks/e2e_completion`.
  - `events-status-webhooks` (weight 10) — label: *"Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change)"*, verified by `webhooks/e2e_status` on compose.
  Both DoDs passed. Feature confidence: **100% / gate 95%**. → the gate said it works. User says it doesn't.
- **suspected root cause (regression-or-gap?)**: **most likely a test-coverage gap**, not a runtime regression. Anchors below; `plan`/`triage` will classify authoritatively.
  - `tests3/reports/release-0.10.0-260417-1454.md:180` — the `events-status-webhooks` pass evidence line reads: *"compose: webhooks/e2e_status: **1 status-change webhook(s) fired: meeting.completed**"*. The step accepted `meeting.completed` (a completion event, already covered by `events-meeting-completed`) as proof of status-change delivery. None of the event types named in the DoD label (`meeting.started` / `bot.failed` / `meeting.status_change`) were actually observed in the report output.
  - `tests3/tests/webhooks.sh:54` configures all four event types; the `e2e_status` step (line 262) pass-condition is *"≥ 1 status-change webhook fired"* — which `meeting.completed` already satisfies via `e2e_completion`, so `e2e_status` is currently a no-op duplicate of `e2e_completion` by accident.
  - No post-green webhook-code commits: since 260417-1454, only `6694502` and `61fbaee` touched webhook-adjacent files, and both are DoD sidecar refactors (`features/webhooks/dods.yaml` move) — no runtime code changed. → a runtime regression is **implausible**; the gate missed the symptom because the test proved the wrong thing.
- **suspected seams (for plan to hypothesise, not to edit now)**:
  - `services/meeting-api/meeting_api/webhooks.py:send_status_webhook` — status dispatch; does it fire for `active` / `stopping` intermediate transitions?
  - `services/meeting-api/meeting_api/webhook_delivery.py` — delivery path; is the `webhook_events` map consulted per event type?
  - `services/api-gateway/main.py:forward_request` — header injection; is `webhook_events` JSON injected intact so meeting-api sees the opt-ins?
  - `services/admin-api/app/main.py:validate_token` — are all four event flags returned?
- **reproducibility confidence**: **high** — the test-gap anchor is deterministic. A tightened `e2e_status` step asserting a *non-completed* event type must arrive should fail today; that is the binding check this pack needs.
- **evidence to bind (candidates for plan's `proves[]`)**:
  - Existing: `webhooks.e2e_status` — needs fix to actually assert a non-completed status event, then re-bind.
  - Likely-new check: something like `webhooks.status_events_non_completed` that asserts *at least one of* `{meeting.started, meeting.status_change, bot.failed}` fires. (plan decides name/shape.)
- **estimated scope**:
  - tests3: extend `tests3/tests/webhooks.sh:e2e_status` to require a non-`meeting.completed` event; add a new step or tighten existing.
  - features/webhooks/dods.yaml: likely tighten the `events-status-webhooks` evidence (or split into two DoDs: default-completion vs opt-in status).
  - services: depends on triage. If runtime is fine, zero service code. If status dispatch is broken for intermediate transitions, 1-3 files in meeting-api webhooks module.
- **`source: human` requirements (per `releases/_template/scope.yaml`)**: this pack, when advanced, will require `gap_analysis:` naming why the matrix missed this, and `new_checks: [...]` enumerating the regression IDs. Both are `plan`-stage artifacts — groom only notes the requirement.

---

### Pack B — Webhook-adjacent open GitHub issues

**All 9 open issues labelled `area: API / Webhooks / MCP`**, triaged by whether they describe a webhook *delivery* bug (pack-A neighbours) or something else:

| # | labels (summary) | title | relevance to this cycle |
|---|------------------|-------|-------------------------|
| 128 | bug, Webhooks/MCP, bots | Zoom bot creation returns 201 before guaranteed runtime failure | **adjacent** — violates bot-lifecycle event contract; if picked, intersects `bot.failed` webhook path (same opt-in the user is reporting). Candidate add-on to pack A. |
| 105 | bug, Webhooks/MCP | [Bug] MCP for MS Teams does not work because agent does not know it needs a passcode | **unrelated to delivery** — MCP tool-shape bug, not a webhook-event bug. Defer. |
| 80  | bug, Webhooks/MCP, good-first-issue (status: accepted) | Admin API Swagger UI Shows Wrong Header in curl Examples | **unrelated** — doc-surface. Defer. |
| 161 | feature, Webhooks/MCP | Accept raw meeting URL on POST /bots endpoint | feature, defer. |
| 160 | feature, Webhooks/MCP, good-first-issue | Add read-only analytics API token to admin-api | already delivered by commit `dc5f846` — **candidate to close**; surface to human. |
| 158 | feature, Webhooks/MCP, complex-task | Per-meeting access control (RBAC) | feature, defer. |
| 139 | feature, Webhooks/MCP, complex-task | Real-time LLM decision listener (Redis pub/sub) | feature, defer. |
| 121 | feature, Webhooks/MCP, bots | Capture meeting metadata from platform | feature, defer. |
| 79  | feature, Webhooks/MCP | Add query param to `GET /meetings` for stale candidates | feature, defer. |

Plus one **non-labelled** open issue that touches webhook machinery:

| # | title | relevance |
|---|-------|-----------|
| 208 | meeting-api: DB connection pool exhaustion from leaked AsyncSession transactions | **already green-gated** in 260417 as `db-pool-exhaustion` (passed lite/compose/helm). Probably just not closed on GitHub. Surface to human for housekeeping. |

**Pack-B recommendation**: fold **#128** into pack A as a co-issue (both live on the status/failed-event path, both arguably testable with a tightened `e2e_status`). Close **#160** and **#208** as already-shipped. Everything else stays in backlog.

---

### Pack C — Non-webhook open signal (awareness only)

Not clustered into packs — flagging for human judgement whether any should steal this cycle from webhooks:

- **Bot lifecycle (8 open bugs)**: #204, #190, #189, #173, #171, #169, #168, #167, #166, #124, #115, #113, #83 — a rich Google-Meet / Teams / Zoom backlog. Out of this cycle's stated scope.
- **Transcription (3 open bugs)**: #157, #146, #104, #96 — Whisper drift, OpenAI transcriber connectivity, transcript visibility.
- **Recording / audio**: #150 (Zoom recording flow).
- **Misc**: #145 (interactive bot endpoints not on api.cloud.vexa).

Per groom contract (`may NOT invent packs`), these are listed not clustered. Human elects if any land in this cycle alongside pack A.

---

## Recommendation (not a decision)

Based on the human signal + the test-gap finding, the cleanest scoped cycle is:

1. **Pack A** (status-webhook coverage) — primary.
2. **Pack B addon**: fold **#128** (Zoom 201-before-failure) into pack A's `proves[]` since they share the `bot.failed` event path.
3. **Pack B housekeeping**: close **#160** (already shipped) and **#208** (already gated) on GitHub — outside the release cycle itself.

Packs C items are deferred unless you want to widen.

---

## Halt

Per stage contract: groom produces this file and **stops**. Scope.yaml is the `plan` stage's output. No code edited, no infra touched.

When you've picked packs, advance with:

```bash
python3 tests3/lib/stage.py enter plan --actor AI:plan
```

Please confirm:
- which pack(s) land in this cycle (A, A+#128, A+others, …)
- whether to close #160 / #208 as part of cycle housekeeping or separately
- any Pack-C items to promote into this cycle
