# Human checklist — 260501-followup / v0.10.5.2 (Part B — eyeroll)

**Filed:** 2026-05-01 (during human stage, Part B)
**Filer:** AI:assist
**Reviewer:** dmitry@vexa.ai

This is the bounded manual eyeroll for v0.10.5.2. Items derived from
`scope.yaml`'s `human_verify[]` plus the standard human-always
checklist for prod ship. Tick each `[x]` once verified. Approve via
`eyeroll_approved: true` in `human-approval.yaml` (Part B) once all
items pass.

## A. Cross-platform smoke (Pack T — SDP-munge)

Already executed in this cycle as live-prod smoke (3 meetings
dispatched, all completed clean). For the audit:

- [x] **GMeet `recording_enabled=true` survives ≥120s** — meeting
      11363 ran 29m13s (chunks 0→59), no `[Vexa] Video transceiver
      stopped` log line, no `Execution context destroyed`,
      meeting reached `completed`.
- [x] **Teams (consumer or corporate) survives ≥60s post-admission** —
      meeting 11364 ran 21m46s on Teams (chunks 0→45), did NOT drop
      in 44ms, reached `completed`.
- [x] **Zoom Web survives first video track event + ≥60s active** —
      meeting 11365 ran 19m57s on Zoom (1 final WAV chunk, paradigm-
      correct), reached `completed`.
- [x] **Helm-mode artifact verification** — `BOT_IMAGE_NAME` env on
      meeting-api pod confirmed `vexaai/vexa-bot:v0.10.5.2` after
      helm rev 53.

## B. Failure-stage tracker (Pack F — `#294`)

- [x] **Static-grep — tracker updates exist for `awaiting_admission`
      AND `active` transitions** — confirmed
      `services/vexa-bot/core/src/services/unified-callback.ts:120`
      contains `advanceLifecycleStage(status)` inside the success
      branch of `callStatusChangeCallback`.
- [ ] **Runtime — force a bot crash post-admission, verify JSONB
      `data.failure_stage == "active"`** — DEFERRED. Will validate
      organically when next prod crash post-v0.10.5.2 occurs (if any).
      If the SDP-munge fix holds, we may not see another crash with
      this stack frame at all.

## C. Customer outreach (release-blocking, must complete pre-ship)

- [x] **Email drafts created** — 5 drafts in Gmail (Ankit, Jeroen,
      Josh, Max, Brian) reviewed + sent by dmitry@vexa.ai.
- [x] **Discord DM sent to @nasnl (Jeroen)** — message ID
      `1499700499086180463` at 09:14:22 UTC.
- [ ] **Customer re-test signal received from at least one affected
      user** — DEFERRED to post-ship. Treat as soft-gate; if no
      re-test feedback in 24h, follow up.

## D. Image artifact integrity

- [x] **Bot image `:v0.10.5.2`** pushed to `vexaai/vexa-bot` on
      Docker Hub. Same sha as `:0.10.6-260501-1128` artifact stamp.
- [x] **Dashboard image `:v0.10.5.2`** pushed to `vexaai/dashboard`.
- [x] **Webapp image `:0.12.0-260501-v0.10.5.2-prod`** pushed.
- [ ] **Orphan `:v0.10.6` Docker Hub tag** — to be deleted in
      teardown. Same sha as v0.10.5.2; not actively pulled. Low
      urgency.

## E. Helm + prod state

- [x] **Helm rev 52** — applied bot v0.10.6 (later renamed v0.10.5.2).
- [x] **Helm rev 53** — applied bot v0.10.5.2 (re-tagged).
- [x] **Helm rev 54** — applied dashboard v0.10.5.2 + webapp
      v0.10.5.2 build.
- [ ] **Visit `dashboard.vexa.ai` and confirm version chip shows
      `v0.10.5.2`** — manual check by human.
- [ ] **Visit `vexa.ai` and confirm version chip in marketing-header
      shows `v0.10.5.2`** — manual check by human.

## F. Stage protocol

- [x] **`emergency-bypass.md` filed** — protocol violations recorded.
- [x] **`validate-report.md` filed** — green verdict with caveats.
- [x] **`code-review.md` filed** — Part A.
- [ ] **`human-approval.yaml`** — both `code_review_approved` AND
      `eyeroll_approved` set to `true` by signer.

## G. Ship-readiness gates (cycle-standard)

- [ ] **`release-validate` (or its bypass equivalent) passes** —
      see validate-report.md verdict (GREEN with caveats).
- [ ] **No new prod crashes with `recording.js:102` Execution
      context destroyed` since 08:31 UTC cutover** — soak comparison
      to be run post-ship.
- [ ] **Git tag `v0.10.5.2` to be created on the merge commit** —
      ship-stage action.
- [ ] **Branch `release/260501-followup` to be merged into `main`** —
      ship-stage action.
- [ ] **vexa-platform commits (`b1d88f4`, `c8438d8`, etc.) pushed
      to `main`** — ship-stage action.
- [ ] **GitHub Release for `v0.10.5.2`** with notes derived from
      this checklist + bypass record — ship-stage action.

## Sign-off

When all items above are `[x]` (or have an explicit DEFER label
acknowledged), edit `human-approval.yaml`:

```yaml
code_review_approved: true   # Part A — done after reading code-review.md
eyeroll_approved: true       # Part B — done after this checklist
```

Items currently DEFERRED (acceptable per emergency-hotfix shape):

- Runtime failure_stage forced-crash test (B.2) — soaks organically
- Customer re-test signal (C.3) — async, treat as soft-gate
- Orphan v0.10.6 Docker tag cleanup (D.4) — teardown
- Manual version-chip verification (E.4 + E.5) — needs human eyes
- Branch merge / git tag / GH Release (G.3-G.6) — ship-stage actions
