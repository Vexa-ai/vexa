# Groom — 260501-followup / v0.10.6 — Post-v0.10.5 customer-driven hardening

> **Public release name (proposed):** `v0.10.6 — Teams reliability + classifier follow-through`
> **Internal release ID:** `260501-followup`
> **Theme:** Followup pass after v0.10.5 ships. Customer reports first, telemetry-led classifier extension second, self-host operator hardening third. No new feature surface beyond what real customers asked for.

| field        | value                                                                                            |
|--------------|--------------------------------------------------------------------------------------------------|
| release_id   | `260501-followup` *(rolled from stale `260422-zoom-sdk` at groom entry; previous slug referred to R6 which shipped as v0.10.5)* |
| stage        | `groom`                                                                                          |
| entered_at   | `2026-05-01T07:35:13Z`                                                                           |
| actor        | `AI:groom`                                                                                       |
| predecessor  | `idle` (after v0.10.5 / R6 ship 2026-04-30)                                                      |
| theme (user) | *"check production meetings telemetry to find issues; groom github issues to have full picture; pay max attention to user issues/comments; check email is any customer reports; also read discord"* |

---

## Inputs (already collected before this groom)

Four streams were swept on 2026-05-01 morning:

1. **Production telemetry** — last-24h meeting outcomes from prod DB. 47 GMeet (14 failed/<null>, 7 awaiting_admission_timeout), 28 Teams (1 failed pre-admission), 3 Zoom. Saved to `tasks/be75ct2dn.output`.
2. **GitHub issues audit** — open issues filtered to non-Dmitry authors with recent comment activity. 16 issues surfaced. Saved to `tasks/bg793o03c.output`.
3. **Email scan** — Gmail searched for customer reports in the v0.10.5 deployment window. contributor-1 (#281), gerryfp (#145), terrence-yu-eb (#280), dev-faisal-shehzad (#282), xjlin0 (#155), Linode 26690727, Stripe webhook health, OeNB customer status.
4. **Discord pull** — last 14 days of #bug-reports, #feature-requests, #general-chat, etc. via bot API. 101 human messages.

**Cleanup pass before grooming**: 5 issues fixed by v0.10.5 already received status comments asking reporters to verify against new image:

- **#171** (Teams anonymous-join modal) — fix `4818e46`. contributor-2's working PR branch was independently solved in v0.10.5 the same day they posted; commented and asked them to retest.
- **#190** (bot doesn't auto-leave / false-high participant count) — fix `c038c42` + audio cross-validation; commented + close-on-verify.
- **#189** (bot leaves when participants join) — same root cause as #190; same fix; commented + close-on-verify.
- **#284** (GMeet `recording_enabled=true` mid-meeting crash) — fix `8ab7f49` SDP-munge revert; commented `status: fixed-pending-verify`.
- **#224** (53min recording cutoff) — classifier `9b16eba` + central classifier `0243737` close FM-001/002/003; commented + recovery-flow ack.

These five do not enter v0.10.6 packs; they're pending customer verification of v0.10.5 fixes.

---

## Scope, stated plainly

The user signal: *"customer reports first, then telemetry, then email, then Discord."* Everything in this groom is filtered to **observed real-customer impact in the last 14 days** or **prod telemetry pattern from the last 24h**. No invented packs.

Two strong signals dominate:

1. **Multiple customers reproducing #281 (Teams 44ms post-admission drop)**. v0.10.5 deferred this issue. Now we have contributor-1 (GH, selfhost+cloud), customer-F (Discord, Vexa Cloud), and an adjacent symptom from malco6838 (Discord, "delete request" on company Teams meetings). This is the lead bug for v0.10.6.

2. **Prod telemetry FM-002 split is real**. Of last-24h `failed/<null>` GMeet meetings (n=14), the recording-aware split shows 8 with `fs=joining` (50% recording-delivered = partial success) vs 6 with `fs=active` (0% recording-delivered = true failure). v0.10.5's classifier closed the orphan-recording case (FM-001) and the recording-aware bucket (FM-002 v2). This data confirms the v2 classifier is correct but reveals a NEW class — `awaiting_admission_timeout` (n=7, GMeet) — which is admission-side, not classifier-side, and warrants its own pack.

These join three customer-feature requests with concrete repro / supplied detail (#280, #270, .earos), and a self-host hardening cluster (#222, #223 + Discord-supplied k8s repros from anurag2069 + sciffer's helm-chart docs↔templates gap).

Operations follow-ups (broken `team@vexa.ai` MX, Linode infra ticket, vexa-lite Mac compat) are listed but **scoped out of v0.10.6 code** — they are non-code action items for the operator.

**Bar for v0.10.6:** A Vexa Cloud customer running Teams enterprise and a self-host operator running k8s + Helm both have a path through their reported failure mode that doesn't require us to chase them in DM. Either the bug is fixed, or the failure is now correctly classified and the operator knows what to do.

---

## Pack T — Teams reliability (LEAD pack) {#pack-t}

### Symptom

Teams meetings drop within 44–51ms after admission, mislabeled as `self_initiated_leave` / `stopped`. Two paying customers reproducing on Vexa Cloud; selfhost reporter (contributor-1) reproducing too. Bot reaches lobby, gets admitted, then immediately exits.

### Owner issue

- **#281** vexa-bot Teams enterprise: meetings drop in 44-51ms after admission — stopped/user mislabeled (OPEN, deferred from v0.10.5)

### Reporters

- **contributor-1** — GitHub #281, hosted + selfhost
- **customer-F** — Discord #bug-reports 2026-04-28, Vexa Cloud, all Teams enterprise meetings
- **malco6838** — Discord #bug-reports 2026-04-29, adjacent symptom: "connects but sees a delete request" on Teams company meetings (DM follow-up pending)

### Likely root-cause hypotheses (to confirm in develop)

1. **SDP-munge regression remnant.** v0.10.5 reverted the transceiver-direction flip for `recording_enabled=true` (`8ab7f49`, root cause for #284). The Teams 44ms drop has a similar shape — health on the bot side, drop right after media negotiation completes. The revert may be incomplete for the Teams code path.
2. **Recording vs. transcribe-only path divergence.** customer-F's repro is `recording_enabled: false, transcribe_enabled: true` — opposite of #284. Suggests two distinct SDP / media-handler bugs that both manifest as "drop right after admission."
3. **Audio-pipeline cross-validation triggering.** `c038c42`'s Layer 2 cross-validation may be firing too eagerly on Teams when the audio pipeline takes longer to come online than on GMeet, causing a healthy-looking exit. Should not happen in 44ms but worth excluding.

### Scope estimate

- **Investigate**: 1–2 days. Need fresh logs from a Vexa Cloud Teams enterprise meeting, and a controlled repro on a corporate vs. consumer Teams URL.
- **Fix**: surgical (revert + re-test) or a small targeted patch in `services/vexa-bot/core/src/platforms/msteams/`. 1 day.
- **Validate**: Teams compose smoke + 3 cloud verification meetings on enterprise. 0.5 day.

### Repro confidence

**HIGH** — 2 customers + 1 already-on-thread reporter. Concrete meeting IDs available in #281 and from customer-F DM.

### Out of scope (this pack)

- Teams chat delivery, avatar publishing, passcode modal — folded under epic #252, not in this cycle's hardening.
- The full SDP-munge architectural rewrite — documented as v0.10.x debt; this pack ships the surgical revert.

---

## Pack C — Classifier coverage extension (post-v0.10.5 follow-through) {#pack-c}

### Symptom

v0.10.5 shipped FM-002 v2 (recording-aware classifier — `9b16eba`, `0243737`). Last-24h prod telemetry confirms it's working: 50% of `fs=joining/failed/<null>` meetings now have a recording delivered, vs 0% before. **But two new gaps surface**:

1. **`awaiting_admission_timeout` (n=7) is not in `completion_reason`**. These meetings are `completed/awaiting_admission_timeout` — the classifier got the status right (admission timeout = lobby never opened) but didn't promote the reason as a typed field. Hides admission-side failure mode from dashboard.
2. **`failed/fs=active/no_recording` (n=6)** is the genuine-loss bucket. Currently classified as `failed/<null>`. Needs its own reason — `audio_pipeline_died` or similar — to distinguish from the `fs=joining` partial-success bucket.

### Owner issue(s)

- **#292** (already filed — recording-aware classifier follow-up) — extend with the two new buckets.
- New issue to file: `meeting-api: promote awaiting_admission_timeout into typed completion_reason field`.

### Reporters

- **Production telemetry** (no individual customer); FM-002 v2 design doc (`tests3/registry/failure-modes.md`); v0.10.5 classifier commits.

### Scope estimate

- **Diagnose**: existing FM-002 v2 logic in `services/meeting-api/.../bot_lifecycle/classifier.py`. Half day.
- **Fix**: add 2 new completion_reason buckets + tests. 1 day.
- **Validate**: telemetry diff showing the same buckets present but now classified non-null. 0.5 day.

### Repro confidence

**HIGH** — telemetry pattern is in last 24h prod data, deterministic by query.

---

## Pack S — Self-host operator hardening (extends epic #257) {#pack-s}

### Symptom

Three concrete operator-side reports during the v0.10.5 window, all distinct sub-classes of the "self-hosting on production-shape infra" problem:

1. **Helm chart docs ↔ templates mismatch** — sciffer_97662 (Discord, 2026-04-22): docs reference enabling a transcription-service flag that isn't in any helm template. Discovered by reading docs end-to-end.
2. **k8s production deploy with custom transcription endpoint** — anurag2069 (Discord, 2026-04-20→21): targeting 1000+ concurrent meetings, deployed via helm, audio captures + lands in MinIO, transcription via `https://api.deepgram.com/v1/listen` returns `fetch failed` → 404. Same shape as #223 (jbschooley earlier). Asked to file GH issue, hasn't yet — but signal is strong.
3. **Coolify reverse-proxy cookie/Secure mismatch** — krzysztofstarzecki (#222): admin panel always 401 after auth. Marked `needs-more-info`; fix proposed (set cookie `Secure` based on inbound scheme, not `NODE_ENV`).

### Owner issues

- **#222** [Hosted] Admin panel always returns 401 after successful authentication
- **#223** [Bug] Transcription not working on k8s deployed vexa
- **#257** Self-hosted operator hardening (epic — covers the contract documentation work)
- New issue to file: `helm chart: transcription-service deployment template missing — docs reference flag with no implementation`

### Reporters

- **krzysztofstarzecki** (#222) — Coolify
- **Harshitpatidarr** (#223) — k8s + Deepgram
- **anurag2069** (Discord) — k8s + Deepgram, repeat pattern, **potential 1000-concurrent enterprise**
- **sciffer_97662** (Discord) — k8s helm operator

### Scope estimate

- **#222 cookie fix**: surgical, 0.5 day.
- **#223 / anurag2069**: contract doc + a small adapter or docs-only "supported endpoints" page. 1 day.
- **Helm transcription-service template**: 1–2 days to template + chart-test + values defaults.
- **Validate**: k8s helm-test + a fresh-Coolify smoke. 0.5 day.

### Repro confidence

**MEDIUM-HIGH** — #222 and #223 have detailed repros; helm gap is doc inspection.

---

## Pack F — Customer-driven feature completions {#pack-f}

### Symptom + reporters

Three feature requests with confirmed customer pull and concrete implementation paths agreed in comments:

1. **#280 Cloud SQL IAM auth** — terrence-yu-eb (enterprise). Cloud SQL Auth Proxy sidecar pattern confirmed. Make `DB_PASSWORD` optional; route through GAC env when set.
2. **#270 Leave meeting if all other users are bots** — jbschooley. Pairs with #285 fix path in v0.10.5; speaker-name regex match on `(fireflies|otter|fathom|read\.ai|notetaker|tactiq|granola|noter|.*\bbot\b.*|.*\.ai$)`.
3. **Transcription model selection** — .earos (Discord, 2026-04-23) — wants flexible model choice for litellm proxy use. Asked to file GH issue. **First-good-issue candidate.** No issue filed yet — scope-gate this one on whether the issue lands by plan stage.

### Scope estimate

- **#280**: 2 days (sidecar pattern, secrets, helm template).
- **#270**: 1 day (regex set + integration with v0.10.5 cross-validation).
- **Model selection**: 0.5 day if .earos files the issue with their use case; otherwise defer to v0.10.7.

### Repro confidence

**N/A** — feature requests, not bugs.

---

## Pack O — Operations & infra (NON-CODE, scoped out of v0.10.6) {#pack-o}

These are real customer-impact items but they're operator-side, not OSS code. Listed here so they don't fall through the cracks; **NOT proposed for v0.10.6 scope.**

| Item | Source | Action |
|---|---|---|
| `team@vexa.ai` MX bounce | ar0x18 Discord 2026-04-30 — had to come to Discord because email bounced | Fix MX/DNS today; verify with test send. **Blocks every release announcement that mentions this address.** |
| Linode Support Ticket 26690727 | Email — LKE us-ord/us-mia provision failures | Reply with current state; decide if we open follow-up |
| Stripe webhook delivery (green.vexa.ai disabled) | Email | Investigate if related to staging env or stale endpoint; re-enable if needed |
| OeNB customer status update | Email — (customer-OeNB-team) | Out-of-band reply by Dmitry |
| vexa-lite on Apple Silicon Mac | gdok1 Discord 2026-04-27 | Decision: test + advertise compat, or update docs to declare AMD64-only. Either way doc-side. |
| Speak API mic permission gap | gdok1 Discord 2026-04-27 — Speak returns 202 but bot has no mic in browser | Worth a triage — but may already be #145-class (`/speak` not on api.cloud.vexa). Cross-check before scoping. |

---

## Pack X — Investigation cycle candidate (DEFER) {#pack-x}

### Symptom

GMeet `failed/fs=active/no_recording` (n=6 in last 24h) is the audio-pipeline-dies-mid-meeting class. v0.10.5 closed the orphan-recording case but not the bots that lose audio capture entirely. Same surface as the long-running #251 epic (audio-capture investigation).

### Owner issue

- **#251** Audio-capture investigation epic (umbrella; covers #115, #204, #237)
- **#286** RaHus 2026-04-30 [Hosted] Fails to join GMeet (likely same class — needs intake)

### Why DEFER

Per project history (epic #251 commentary): audio-capture failures need an instrumentation pass before a fix pass. Bundling instrumentation into a hardening release will double the cycle length. Recommend dedicated investigation cycle after v0.10.6, with explicit "instrument first, fix second" structure.

---

## Other deferred items (carry-forward, no pack)

These were filed during v0.10.5 cycle or earlier and remain deferred for stated reasons. Not proposing them as v0.10.6 packs unless promoted by user.

- **#155** Hallucination blocklist 10+ languages — community-contributed, awaiting xjlin0 + others' contributions.
- **#232** Transcription model selection (JanWe92) — superset of .earos's request; if Pack F's .earos issue lands, fold this in.
- **#238** `voice_agent_enabled` grab-bag — superseded by capability split #246; tracked there.
- **#245** SpeakerStreamManager env-configurable — folded into #251 epic.
- **#198** `make all` non-interactive shell hang — file removed in `b9c8f14` 2026-04-05; awaiting victalejo confirmation. Auto-close if no response in 14 days.
- **#282** AWS S3 selfhost recording — user-resolved by rolling to MinIO with newer code. **Closeable as user-resolved** if confirmed by reporter or stale-bot.
- **Zoom recording playback fragmentation** — already filed as deferred from R6 (`tests3/releases/260427/zoom-recording-playback-fragmentation-gap.md`). Investigation cycle candidate alongside #251.

---

## Proposed pack ordering for plan stage

If the user accepts all three primary packs:

| Order | Pack | Rationale |
|---|---|---|
| 1 | **Pack T** Teams reliability | Lead — 2+ customers reproducing, Cloud SLA-adjacent |
| 2 | **Pack C** Classifier coverage extension | Closes telemetry observability gap; small surface |
| 3 | **Pack S** Self-host hardening | Operator-side; helm-chart gap is concrete |
| 4 | **Pack F** Customer features | Optional — drop #280 to v0.10.7 if cycle gets long |

**Pack X / audio-capture: DEFER explicitly** to its own investigation cycle.
**Pack O / ops: handle out-of-band** by Dmitry today (email bounce especially).

---

## Cleanup actions before plan stage

- [x] Comment v0.10.5-fix status on #171, #190, #189, #224, #284 (done at groom entry).
- [ ] DM follow-up to **workaddict** (Discord 2026-04-30 — paid customer "try later" error, no response yet).
- [ ] DM follow-up to **malco6838** (Discord 2026-04-29 — Teams delete-request, asked for meeting ID).
- [ ] Reply to **contributor-2** on #171 thanking + asking for v0.10.5 verification (the `fix(msteams) modal` commit landed exactly their fix path).
- [ ] Cross-check **gdok1's Speak API** report against #145 — same class or distinct?
- [ ] Encourage **anurag2069** to file the k8s + Deepgram issue (he said he would; Pack S blocked on his details).
- [ ] Encourage **.earos** to file the model-selection issue (Pack F's lightest item depends on it).
- [ ] Fix `team@vexa.ai` MX **before plan stage** — release-comm channel currently broken.

---

## HALT

`groom` stage objective complete. Material is ready for human pick-and-approve. Next legal stage: `plan` (write `scope.yaml` + `plan-approval.yaml` for the chosen packs).

Human picks which packs to advance:
- **Pack T**: lead — strong recommend
- **Pack C**: small + telemetry-driven — strong recommend
- **Pack S**: operator-side — recommend if scope budget allows
- **Pack F**: feature requests — optional, can drop to v0.10.7
- **Pack X / Pack O**: do not advance to v0.10.6 plan; handle elsewhere

Awaiting human signal to enter `plan`.
