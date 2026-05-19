# Stale-issue audit sweep — decisions

For v0.10.6.1 prove-check `STALE_AUDIT_SWEEP_DECISIONS_FILED`. CEO reviews, then applies via `gh issue close` / `gh issue edit --add-label "reconfirmed"` from the worktree.

Authored 2026-05-11. Reviewer: Claude (sub-deliverable of T-038).

**Quick summary up front:** Four of the five issues are real, well-triaged, and already tracked in maintainer-authored epics (#255, #253, #256) — these are RECONFIRMs (no closure, just tag + cross-reference). One issue (#198) has its root-cause file deleted and its specific symptom no longer reproduces — that one is a CLOSE with a thoughtful note explaining the transformation.

---

## #166 — Bot exits with admission_false_positive in Google Meet waiting room

**Decision:** RECONFIRM

**Rationale (≤120 words):** Real bug, code path still live. Verified `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:143` still calls `gracefulLeaveFunction(page, 0, "admission_false_positive")`. The maintainer comments (2026-04-24 + 2026-04-25) already deferred this to **epic #255 — Bot lifecycle refinement** (Phase 2: Admission detection). #255 is OPEN with a multi-phase plan, not scoped into v0.10.6.1. The bug remains real but is correctly cycle-deferred. Reconfirming for the epic-#255 cycle when it picks up; no new comment needed beyond the tag (the epic already explicitly lists this as Phase 2 input).

**Comment draft (for `gh issue comment`):**
> Reconfirming this is still a real issue (the `admission_false_positive` code path in `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:143` is live), and it remains tracked under epic #255 — Bot lifecycle refinement, Phase 2 (Admission detection). Not in scope for the v0.10.6.1 hardening cycle. Will be picked up when the lifecycle-refinement epic enters a release cycle. Leaving open with the reconfirm tag for backlog discoverability.

**Apply with:**
```bash
gh issue edit 166 --repo Vexa-ai/vexa --add-label "reconfirmed-stale-audit-2026-05-11"
gh issue comment 166 --repo Vexa-ai/vexa --body "Reconfirming this is still a real issue (the \`admission_false_positive\` code path in \`services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:143\` is live), and it remains tracked under epic #255 — Bot lifecycle refinement, Phase 2 (Admission detection). Not in scope for the v0.10.6.1 hardening cycle. Will be picked up when the lifecycle-refinement epic enters a release cycle. Leaving open with the reconfirm tag for backlog discoverability."
```

---

## #113 — Bot remains in waiting-for-admission state after admission failure (container already dead)

**Decision:** RECONFIRM

**Rationale (≤120 words):** Partial architectural movement landed (the runtime-api rewrite added `reconcile_state()` and `handle_container_exit()` — verified at `services/runtime-api/runtime_api/lifecycle.py:179` and `:272`), but reconciliation **runs at startup only**, which is exactly the gap the original acceptance criteria flag ("reconciler/health-check logic updates stale runtime state within expected interval"). The continuous-state-sync ask is unmet. Maintainer already deferred to **epic #255**, Phase 3 (Continuous state reconciliation). Same RECONFIRM logic as #166: real, partially addressed, fully-tracked in an open epic, not in v0.10.6.1 scope.

**Comment draft (for `gh issue comment`):**
> Reconfirming. Partial progress landed via the runtime-api rewrite (`reconcile_state()` + `handle_container_exit()` at `services/runtime-api/runtime_api/lifecycle.py:179` / `:272`), but reconciliation is startup-only — the continuous-interval acceptance criterion remains unmet. Tracked under epic #255 — Bot lifecycle refinement, Phase 3 (Continuous state reconciliation). Not in scope for v0.10.6.1. Will be picked up when the lifecycle-refinement epic enters a release cycle. Reconfirm tag added for backlog discoverability.

**Apply with:**
```bash
gh issue edit 113 --repo Vexa-ai/vexa --add-label "reconfirmed-stale-audit-2026-05-11"
gh issue comment 113 --repo Vexa-ai/vexa --body "Reconfirming. Partial progress landed via the runtime-api rewrite (\`reconcile_state()\` + \`handle_container_exit()\` at \`services/runtime-api/runtime_api/lifecycle.py:179\` / \`:272\`), but reconciliation is startup-only — the continuous-interval acceptance criterion remains unmet. Tracked under epic #255 — Bot lifecycle refinement, Phase 3 (Continuous state reconciliation). Not in scope for v0.10.6.1. Will be picked up when the lifecycle-refinement epic enters a release cycle. Reconfirm tag added for backlog discoverability."
```

---

## #128 — Zoom bot creation returns 201 before guaranteed runtime failure

**Decision:** RECONFIRM

**Rationale (≤120 words):** Default Zoom path is now Web Client (no SDK creds needed), so most users don't hit the original 201-then-fail symptom. But the legacy SDK path (`ZOOM_SDK=true`) **still** has the 201-then-runtime-fail behavior — verified at `services/meeting-api/meeting_api/meetings.py:1141-1146` where the env-var forward happens without a pre-flight credential check. The most recent maintainer comment (2026-04-27) explicitly says "pre-flight credential validation in meeting-api remains a Wave 2/3 follow-up." Tracked under **epic #253 — Zoom Meeting SDK recovery**, Phase 3 (Pre-flight #128). Real, narrowed, fully-tracked in an open epic, not in v0.10.6.1 scope.

**Comment draft (for `gh issue comment`):**
> Reconfirming. The default `platform=zoom` route (Web Client) no longer needs SDK creds and doesn't hit the original symptom, but the legacy SDK path (`ZOOM_SDK=true`) at `services/meeting-api/meeting_api/meetings.py:1141-1146` still returns 201 without a pre-flight credential check — runtime fail comes later. Tracked under epic #253 — Zoom Meeting SDK recovery, Phase 3 (Pre-flight #128). Not in scope for v0.10.6.1; will be picked up when the Zoom SDK recovery epic enters a release cycle. Reconfirm tag added for backlog discoverability.

**Apply with:**
```bash
gh issue edit 128 --repo Vexa-ai/vexa --add-label "reconfirmed-stale-audit-2026-05-11"
gh issue comment 128 --repo Vexa-ai/vexa --body "Reconfirming. The default \`platform=zoom\` route (Web Client) no longer needs SDK creds and doesn't hit the original symptom, but the legacy SDK path (\`ZOOM_SDK=true\`) at \`services/meeting-api/meeting_api/meetings.py:1141-1146\` still returns 201 without a pre-flight credential check — runtime fail comes later. Tracked under epic #253 — Zoom Meeting SDK recovery, Phase 3 (Pre-flight #128). Not in scope for v0.10.6.1; will be picked up when the Zoom SDK recovery epic enters a release cycle. Reconfirm tag added for backlog discoverability."
```

---

## #96 — Transcripts hidden when session_uid mismatch between MeetingSession and Transcriptions

**Decision:** RECONFIRM

**Rationale (≤120 words):** The original code paths cited in the issue body have moved (transcription-collector folded into `meeting-api/collector/`), but the underlying logic is identical: `_get_full_transcript_segments` in `services/meeting-api/meeting_api/collector/endpoints.py:155-175` still resolves `absolute_start_time` only when `session_times.get(seg.session_uid)` is present, and falls through to `abs_start = abs_end = None` when missing. The dashboard hook at `services/dashboard/src/hooks/use-live-transcripts.ts` still relies on `absolute_start_time` for time-based ops. Maintainer already deferred to **epic #256 — Segment reconciliation research**, Phase 1 (explicitly named as prerequisite for the rest of the epic). Real, unchanged, fully-tracked, not in v0.10.6.1 scope.

**Comment draft (for `gh issue comment`):**
> Reconfirming. The transcription-collector code folded into `services/meeting-api/meeting_api/collector/`, but `_get_full_transcript_segments` still drops `absolute_start_time` to None when `session_times.get(seg.session_uid)` returns nothing (verified at `services/meeting-api/meeting_api/collector/endpoints.py:155-175`). Same bug shape. Tracked under epic #256 — Segment reconciliation research, Phase 1 (explicitly named as the prerequisite for downstream caption-merge work). Not in scope for v0.10.6.1; will be picked up when the segment-reconciliation epic enters a release cycle. Reconfirm tag added for backlog discoverability.

**Apply with:**
```bash
gh issue edit 96 --repo Vexa-ai/vexa --add-label "reconfirmed-stale-audit-2026-05-11"
gh issue comment 96 --repo Vexa-ai/vexa --body "Reconfirming. The transcription-collector code folded into \`services/meeting-api/meeting_api/collector/\`, but \`_get_full_transcript_segments\` still drops \`absolute_start_time\` to None when \`session_times.get(seg.session_uid)\` returns nothing (verified at \`services/meeting-api/meeting_api/collector/endpoints.py:155-175\`). Same bug shape. Tracked under epic #256 — Segment reconciliation research, Phase 1 (explicitly named as the prerequisite for downstream caption-merge work). Not in scope for v0.10.6.1; will be picked up when the segment-reconciliation epic enters a release cycle. Reconfirm tag added for backlog discoverability."
```

---

## #198 — make all: infinite loop in test step on non-interactive shells (run_vexa_interaction.sh)

**Decision:** CLOSE

**Rationale (≤120 words):** The specific script cited (`testing/run_vexa_interaction.sh`) was deleted in commit `b9c8f14` on 2026-04-05 — three days *before* this issue was filed. The `testing/` directory no longer exists. `make all` now goes through `deploy/compose/Makefile`, whose `test:` target is non-interactive (curl health-checks). The specific symptom in the report — the infinite-loop spam of `Invalid Google Meet ID format` — cannot reproduce on current main. **Note for transparency:** the `preflight:` target still has `read -r < /dev/tty` for transcription-token entry (lines 108 / 113), which would fail differently in a non-TTY shell — that's a distinct, fast-fail concern worth its own issue if it bites. Closing this one against its specific symptom.

**Comment draft (for `gh issue close --comment`):**
> Closing — the specific failure cited here doesn't reproduce on current main. `testing/run_vexa_interaction.sh` was deleted in commit `b9c8f14` on 2026-04-05 (about three days before this issue was filed), and the `testing/` directory no longer exists. `make all` now invokes `deploy/compose/Makefile`'s `test:` target, which is non-interactive (curl-based health checks against the running stack) — no `read -p` loop, no `Invalid Google Meet ID format` spam path.
>
> Thank you for the very thorough report; the diagnosis was correct against the codebase at filing time. The script was being retired in parallel and we didn't catch the cross-reference.
>
> Heads-up for anyone hitting a related symptom in the future: `deploy/compose/Makefile` lines 108/113 still have `read -r < /dev/tty` in the `preflight:` target for transcription-token entry. That would fail differently (it should fast-fail rather than infinite-loop), but if you're seeing a non-interactive hang in a related place, please file a fresh issue with the current log output — the surface has moved enough that it deserves its own bug.

**Apply with:**
```bash
gh issue close 198 --repo Vexa-ai/vexa --comment "Closing — the specific failure cited here doesn't reproduce on current main. \`testing/run_vexa_interaction.sh\` was deleted in commit \`b9c8f14\` on 2026-04-05 (about three days before this issue was filed), and the \`testing/\` directory no longer exists. \`make all\` now invokes \`deploy/compose/Makefile\`'s \`test:\` target, which is non-interactive (curl-based health checks against the running stack) — no \`read -p\` loop, no \`Invalid Google Meet ID format\` spam path.

Thank you for the very thorough report; the diagnosis was correct against the codebase at filing time. The script was being retired in parallel and we didn't catch the cross-reference.

Heads-up for anyone hitting a related symptom in the future: \`deploy/compose/Makefile\` lines 108/113 still have \`read -r < /dev/tty\` in the \`preflight:\` target for transcription-token entry. That would fail differently (it should fast-fail rather than infinite-loop), but if you're seeing a non-interactive hang in a related place, please file a fresh issue with the current log output — the surface has moved enough that it deserves its own bug."
```

---

## Summary

| Issue | Decision | One-line rationale |
|---|---|---|
| #166 | RECONFIRM | Code path live (`meetingFlow.ts:143`); tracked in epic #255 Phase 2. |
| #113 | RECONFIRM | Reconcile-on-startup landed; continuous-interval gap remains; tracked in epic #255 Phase 3. |
| #128 | RECONFIRM | Default Zoom Web bypasses symptom; legacy SDK path still 201-then-fail; tracked in epic #253 Phase 3. |
| #96 | RECONFIRM | Code folded into `meeting-api/collector/`; same drop-on-missing-session_uid logic; tracked in epic #256 Phase 1. |
| #198 | CLOSE | Root-cause file deleted pre-filing; symptom path replaced by curl checks; surface unrelated to current `make all`. |

**CLOSE: 1 · RECONFIRM: 4**

### Notes for the CEO

1. **Apply order is irrelevant** — each command is independent.
2. **The `reconfirmed-stale-audit-2026-05-11` label may not exist** in the repo yet. If `gh issue edit --add-label` fails because the label doesn't exist, the worktree CEO can create it once with `gh label create "reconfirmed-stale-audit-2026-05-11" --color "fbca04" --description "Triaged + reconfirmed in 2026-05-11 stale-audit sweep" --repo Vexa-ai/vexa`, then re-run the four edits.
3. **One side-finding worth its own future issue** — `deploy/compose/Makefile` `preflight:` reads from `/dev/tty` for transcription-token entry; non-TTY shells would still fail at that step. Not in the original five, but discovered while verifying #198. Worth a fresh ticket when bandwidth allows.
