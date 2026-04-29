# Bot-lifecycle failure modes — registry

Operational catalog of failure modes observed in production or live testing.

**Standing rule** (team-lead, 2026-04-28): every prod failure surfaced by
PLATFORM (or anyone) lands here as a tracked entry. PLATFORM owns
ensuring nothing slips through unrecorded; OSS owns this file. Aggregate
patterns (rates, buckets) are forwarded to DATA separately — raw entries
stay here.

---

## Index

| ID | First observed | Platform | Symptom (one-line) | Status |
|---|---|---|---|---|
| `FM-001` | 2026-04-28 | google_meet | gmeet end-of-meeting page navigation crashes the bot — successful meetings painted as failed | partial-fix-in-0.10.5 |
| `FM-002` | 2026-04-28 (via 11161) | * | Bot exits with non-allowlisted `payload.reason` bypass Pack J classifier — `status=failed, completion_reason=NULL` | open |
| `FM-003` | 2026-04-28 (via 11161) | * | `failure_stage` from bot payload doesn't reflect actual `meeting.status` — read-side allowlist tolerates, write-side never corrects | open |
| `FM-004` | 2026-04-19 (onset; observed via FM-001) | google_meet | gmeet end-of-meeting page navigation — same trigger as FM-001; tracks the bot-side crash signature catalog | open (merged into FM-001 scope) |
| `FM-005` | 2026-04-28 (ARCH-2 review of Option C) | n/a | Option C cosmetic carry-forward: inline-rebuilt classifier dict + silent-default failure_stage helper | open (v0.10.6) |
| `FM-006` | 2026-04-28 (ARCH-2 spec, north-star check) | n/a | Full Recall-parity envelope — `bot_lifecycle: { joined_at, left_at, recorded_seconds, segment_count, exit_reason, failed_at_stage, status_path }` typed object on `MeetingResponse` + webhook payload | open (v0.10.6 / Pack Σ, blocks on FM-007) |
| `FM-007` | 2026-04-28 (team-lead approved, Pack Σ keystone) | n/a | Shared `bot-lifecycle-vocab` package — single source of exit-reason strings + completion-reason mapping + human-readable descriptions; consumed by bot + meeting-api + SDK type-gen + dashboard. Kills "two enums secretly meant to match" antipattern at source. | open (v0.10.6 / Pack Σ keystone — starts post-0.10.5-ship; blocks FM-006/008/005-items) |
| `FM-008` | 2026-04-29 (OSS-2 + team-lead redesign session) | n/a | Bot-lifecycle outcome-taxonomy redesign — 4-bucket model (success/user_aborted/environment_blocked/software_fault) + ~30 sub-causes + `audio_confirmed_at` flag + `needs_help` rename + 6-surface delivery framing | open (v0.10.6, blocks on FM-007) |

Status vocabulary: `open` · `pending-data` · `fix-in-flight` · `fixed-in-X.Y.Z` · `partial-fix-in-X.Y.Z` · `wontfix` · `pending-decision`.

`partial-fix-in-X.Y.Z` is used when X.Y.Z fixes some sub-symptoms (e.g. user-visible) but leaves underlying causes open — entries should call out exactly which sub-symptoms are fixed and which remain.

---

## FM-001 — gmeet end-of-meeting page navigation crashes the bot

```yaml
id: FM-001
first_observed: 2026-04-28T11:31:02Z
platform: google_meet
status: partial-fix-in-0.10.5
trigger: gmeet-post-meeting-page-navigation     # PLATFORM repro evidence 2026-04-28
trigger_subclasses:
  - meeting_ended_immediate_navigation         # short gap, ~70-85% of cases
  - meeting_ended_then_alone_then_navigation   # long gap, ~15-30% of cases
fixed_subsymptoms:
  - orphan_recording_dashboard_empty           # fa88c2c Pack E.1.a + 4982cd5 hardening
open_subsymptoms:
  - mislabeled_as_failed_when_actually_succeeded   # most are success cases (recorded_then_user_stopped / host_ended / left_alone)
  - failure_stage_mislabeled_as_joining            # see FM-003 (write-side root cause)
  - exit_bypasses_pack_j_classifier                # see FM-002 (broader class — completion_reason=NULL)
  - bot_loses_last_5_30s_of_audio_after_navigation # post-crash audio not captured even with Pack E.1.a
linked_fixes:
  - {commit: fa88c2c, pack: E.1.a, closes: orphan_recording_dashboard_empty}
  - {commit: 4982cd5, pack: Q+E.1.a, closes: media_files_audio_video_stale_read_race}
  - {commit: 3b54143, closes: failure_stage_render_path}     # dashboard TS narrowing
  - {commit: f0e618a, closes: failure_stage_dashboard_render_partial}  # Pack R allowlist
deferred_fixes:
  - target: v0.10.5 (scope reopened)
    pack: Σ (lifecycle taxonomy redesign)
    closes: mislabeled_as_failed_when_actually_succeeded
  - target: v0.10.6+
    pack: bot_navigation_classifier
    closes: rare_genuine_crash_distinguished_from_normal_end
recommended_classification_v0.10.6_taxonomy:
  short_gap_subclass: success.recorded_then_user_stopped OR success.recorded_then_host_ended
  long_gap_subclass: success.recorded_then_left_alone
  rare_genuine_crash: software_fault.bot_crashed_in_call   # only when nav URL is NOT a gmeet post-call path
linked_classifier_path: services/meeting-api/meeting_api/callbacks.py:52   # _classify_stopped_exit
linked_bot_path: services/vexa-bot/core/src/platforms/googlemeet/recording.ts:124   # page.evaluate that throws
linked_meetingflow_path: services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:196-226
linked_framenavigated_handler: services/vexa-bot/core/src/index.ts:2216   # exists but filters main-frame events; remove the filter for diagnostics
prod_observations:
  - meeting_id: 11161
    timestamp: 2026-04-28T11:31:02Z
    user_id: 1212
    user_email: jeroen.nas@gmail.com
    duration_active_s: 1780                # 29m 40s
    transcript_segments: 197
    last_segment_to_crash_gap_s: 14        # short-gap subclass — meeting ended, bot didn't survive nav
    s3_chunks: 60
    s3_bytes: 31457280                     # ~30 MB
    images:
      meeting_api: vexaai/meeting-api:0.10.0-260426-2358
      vexa_bot: vexaai/vexa-bot:0.10.0-260426-2358
    namespace: vexa-production
prod_aggregate_14d_PLATFORM_2026_04_28:
  total_instances: 84                       # google_meet only; 1 msteams instance separately catalogued in FM-004
  onset: 2026-04-19                         # rate was 0% pre-onset; sharp transition
  current_rate_today: 0.258                 # 8/31 gmeet sessions
  rate_floor_since_onset: 0.07
  rate_ceiling_since_onset: 0.32
  gap_distribution_seconds:
    p10: 15
    median: 75
    p90: 769
    bimodal: true                           # short-gap (~13-75s) and long-gap (~5-13min) clusters
  deterministic_repro: |
    User 1970 retried same gmeet URL `fig-sqtc-gri` 8x → 8 identical crashes.
    Trigger is in the meeting page itself, not random network noise.
```

### Symptom (user-visible)

Meeting marked `failed`. Dashboard shows empty recording even though the
user watched the bot transcribe live for 30 minutes. The transcript
itself was delivered (197 segments, persisted via Redis pub/sub → DB)
but is not exposed alongside the recording.

### Symptom (data state, pre-0.10.5)

- 197 transcript rows in `transcriptions` (live transcription path worked).
- 60 webm chunks (~30 MB) durable in S3 at
  `vexa-recordings/recordings/<user_id>/<storage_id>/<session_uid>/audio/000000.webm` … `000059.webm`.
- 0 rows in `recordings` table for `meeting_id`.
- Recording reference exists ONLY inside `meetings.data.recordings[0]`
  JSON, with `status: "in_progress"`, `media_files: []`, `completed_at: null`.
- Post-meeting webhook fires (200 OK) but with empty `media_files`,
  so downstream sees nothing.

### Symptom (status surface)

- `meetings.status_transition[]`: `requested → joining → awaiting_admission → active → failed`
  — last transition at exit time.
- `meetings.failure_stage`: `"joining"` (mislabeled — meeting was
  active for 30 min; see **FM-003** for the write-side root cause).
- `meetings.completion_reason`: **NULL** — the bot's `payload.reason="post_join_setup_error"`
  is not in the Pack J allowlist at `callbacks.py:262-271`, so the exit
  takes the `else` branch at `callbacks.py:311-326` which writes
  `failure_stage` from payload but never sets `completion_reason`. See
  **FM-002** for the broader class (the `else` branch's silent NULL bucket
  is 182 rows / 7d in prod).
- The bot DID populate `payload.reason="post_join_setup_error"` and
  `payload.error_details` (Playwright stack trace, see below). Both
  surface in `meetings.data.last_error.reason` /
  `meetings.data.error_details`. The data is captured; the gap is
  that the central classifier never sees this path, so
  `completion_reason` stays NULL on the meeting row itself.

(Earlier draft of this entry incorrectly inferred `completion_reason="post_join_setup_error"`
based on the bot payload — corrected 2026-04-28 after PLATFORM pulled
the actual prod row. The bot reports `reason`, not `completion_reason`;
the classifier is supposed to derive one from the other, and for this
exit path it doesn't.)

### Bot-side root cause — UPDATED 2026-04-28 (PLATFORM evidence)

`page.evaluate: Execution context was destroyed, most likely because of a navigation`
thrown from inside `startGoogleRecording` at
`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:124`.

The throw lands in the `Promise.race([strategies.startRecording, removalPromise])`
at `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:196-199`,
falls through to the catch at `:218-226`, and bot calls
`gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails)`
cleanly.

**The trigger is gmeet's end-of-meeting page navigation** (PLATFORM
repro 2026-04-28, n=23 sampled). When a meeting ends — last participant
leaves, host clicks "End call", or bot is left alone past gmeet's idle
threshold — gmeet auto-navigates the call UI to its post-meeting screen
("How was your call?" / "You left the call" / `/landing`). That
navigation destroys the Playwright execution context that
`startGoogleRecording` is awaiting on, and the throw is what reaches our
catch.

Two sub-classes by gap from last transcript segment to crash:

- **Short gap (~13-75s, ~70-85% of cases).** Meeting ended; gmeet
  immediately navigated. Last transcript line tends to be a goodbye
  ("okay cool talk to you later bye", "до свидания. пока", "Have a
  nice day. Bye-bye."). This is **a successful meeting**, not a crash.
- **Long gap (~5-13min, ~15-30% of cases).** Bot was left alone after
  the meeting wrapped, then either gmeet's auto-leave-when-alone fired
  or our `max_time_left_alone: 900000ms` (15min) bot timer fired a
  leave action that hits the same crash path. Also normal end-of-call.

Onset 2026-04-19 was sharp (0% → 6% → 25%) and consistent with a Google
Meet rollout that changed post-call navigation. Pre-04-19, the call UI
likely unmounted in-place; post-04-19, it's a hard navigation. We have
no code change in that window that could explain the rate flip.

A `page.on('framenavigated')` handler exists at
`services/vexa-bot/core/src/index.ts:2216` but filters out main-frame
events. Removing the filter and reading `frame.url()` at the navigation
moment is plausibly a 10-line change that distinguishes "meeting
ended" (URL contains `/landing`, `/_meet/`, etc.) from "real crash"
(chrome-error://, blank, etc.) — see "Bot-side hardening" below.

(See **FM-004** for the bot-side crash signature catalog as its own
entry — the trigger and detection signature live there.)

### Server-side root cause (orphan recording, pre-0.10.5)

Pre-Pack-E.1.a (`fa88c2c`) policy: intermediate chunks
(`is_final=false`) upload to MinIO/S3 but `media_files` only updated
on `is_final=true`. When the bot died mid-recording, the upload
window between last chunk and `is_final=true` never closed —
`media_files=[]` despite N chunks in S3.

Compounded by a stale-read race on the JSONB write path: two
concurrent uploads (audio chunk + video chunk) both saw `media_files=[]`
pre-lock; one wrote `[audio]`, the other (using its stale snapshot)
wrote `[video]` — losing the audio entry despite both S3 uploads
succeeding. PLATFORM caught this in code review (`#272 issuecomment-4327366063`).

### Repro

1. Spawn a gmeet bot via `POST /bots`.
2. Let the bot reach `active` and start recording.
3. Trigger a page navigation inside the gmeet client mid-recording
   (any action that destroys the `page.evaluate` execution context —
   picture-in-picture toggle has been observed; other triggers
   suspected, not all confirmed).
4. **Pre-0.10.5:** observe `recordings[0].media_files=[]` despite N
   chunks in S3.
5. **Post-0.10.5:** observe `recordings[0].media_files` populated with
   latest chunk's storage_path; `status: "in_progress"`; dashboard
   shows partial recording.

Synthetic repro for the bot crash itself is harder — needs a
controllable navigation injection in the gmeet UI or an
instrumented Playwright reproducer that calls `page.goto()` mid-evaluate.

### What 0.10.5 fixes / doesn't (UPDATED post-trigger-identification)

| sub-symptom | fixed in 0.10.5? | mechanism |
|---|---|---|
| Orphan recording (chunks in S3, no DB linkage) | YES | `fa88c2c` Pack E.1.a — per-chunk `media_files` write under `SELECT FOR UPDATE`. Dashboard shows partial recording. Hardened in `4982cd5`. |
| Mislabeled-as-failed for what is actually a successful meeting | YES (with Pack Σ now in scope) | Pack Σ's 4-bucket taxonomy classifies these as `success.recorded_then_user_stopped` / `recorded_then_host_ended` / `recorded_then_left_alone` based on context. The framenavigated URL classifier (10-line bot patch) makes the cut precise; without it Pack Σ can still fall back to "duration ≥ N min + transcripts > 0 → success" heuristic. |
| `failure_stage: "joining"` mislabel | YES (FM-003 patch within Pack Σ scope) | Server-side derive from `meeting.status` at write time. |
| Last 5-30s of audio lost after gmeet navigation | NO | Bot exits when the page navigates; Pack E.1.a saves what landed in S3 before the exit, but post-nav audio isn't captured. v0.10.6+ bot-side hardening (framenavigated auto-restart or CDP-driven external capture) is the path. |
| Rate of FM-001 incidents (currently ~25% of gmeet sessions) | NO | We don't fix the trigger; gmeet rolled out the navigation. We classify it correctly so users see green checks for what worked. |

### Bot-side hardening — reframed after trigger identification

Trigger is now known (gmeet end-of-meeting nav), so "no fix without
repro" no longer applies. The fix path becomes a question of WHERE
to do the classification:

1. **Server-side** (lowest risk, lands inside Pack Σ). Meeting-api routes
   `payload.reason="post_join_setup_error"` through Pack J when the
   last transcript segment is recent (e.g. < 5 min before exit) →
   classify as `success.recorded_then_user_stopped`. No bot change. This
   is the FM-002 allowlist extension applied to one specific reason
   string, with a recency-of-segments rule. Closes ~85% of FM-001
   instances (the short-gap subclass) without touching the bot.

2. **Bot-side framenavigated classifier** (10-line change). Remove the
   main-frame filter at `index.ts:2216`; on navigation, read
   `frame.url()`, classify against known gmeet post-call paths
   (`meet.google.com/landing`, `/_meet/...`), and call gracefulLeave with
   a specific reason (`reason="meeting_ended_via_navigation"`) instead
   of letting the throw masquerade as `post_join_setup_error`.
   Cleaner signal end-to-end. Lands as a pair with #1 — server-side
   classifier reads the new bot reason without needing the recency
   heuristic.

3. **Bot-side framenavigated auto-restart** (medium complexity). When
   the nav is same-origin and the meeting clearly hasn't ended (segments
   still flowing), re-inject the recorder. Only useful if we hit a
   class of mid-meeting navs that aren't end-of-call (none observed
   yet). Defer until there's evidence.

4. **CDP-driven external capture** (heavy). Capture audio at
   browser-level handle, invariant to in-page nav. v0.11.x territory.

Recommendation: ship #1 + #2 together as part of the v0.10.5 Pack Σ
develop iter. Both are small, both reinforce each other (#2 makes #1
deterministic instead of heuristic). #3 and #4 wait for evidence.

### Diagnostic instrumentation needed (next live capture)

Bot pod logs are deleted post-failure (k8s pod deletion + no log
aggregator in the cluster). Two options to capture nav URLs for
post-mortem:

- One-line: enable main-frame logging at `index.ts:2216`. Captures
  destination URL into stdout while the bot is alive — runtime-api
  proxies that. Won't survive pod deletion alone.
- Pair with a Redis-stream archive on the exit-callback path
  (~50 LOC) — bot stdout lines tagged with `[NAV]` get XADDed to
  `bot_diagnostic:{meeting_id}` keyed for 24h, archived into
  `meetings.data.bot_diagnostic` on exit-callback.

Worth landing as part of the same Pack Σ iter if we want robust
post-mortem on the next FM-004 occurrence (or any new failure class).

### Recommended classification under v0.10.6 Pack Σ taxonomy

```
outcome:        success
outcome_class:  normal
cause:          recorded_then_user_stopped     # short-gap subclass (most cases)
                recorded_then_host_ended       # if host-ended signal is detectable
                recorded_then_left_alone       # long-gap subclass
                recorded_then_bot_crashed      # rare — only when nav URL is NOT a known gmeet post-call path
```

For 11161 specifically: short-gap subclass (14s from "okay cool talk to
you later bye" → crash). Classification: `success.recorded_then_user_stopped`.
Not `software_fault.bot_crashed_in_call` — the meeting succeeded, the
bot just didn't recognize gmeet's signal.

(Earlier draft of this section recommended `success.recorded_then_bot_crashed`
as a compromise. Corrected 2026-04-28 after PLATFORM evidence reframed
the trigger from "unknown crash" to "normal end-of-call signal we don't
read".)
Lifecycle README argues no.

---

## How to add an entry (PLATFORM → OSS handoff format)

For each prod failure observed, send OSS a message containing:

| field | description |
|---|---|
| `session_id` / `session_uid` / `container_id` | identifiers for cross-referencing |
| `timestamp` (UTC) | when the failure was observed; used as `first_observed` if new |
| `duration_active_s` | active phase duration (seconds) |
| `user_id` / `user_email` | for grouping per-user patterns |
| `platform` / `native_id` | gmeet / zoom / msteams / etc. |
| `images` | `meeting-api` and `vexa-bot` image tags |
| `namespace` | `vexa-production` / `vexa-staging` / etc. |
| symptom (user-visible) | one paragraph |
| symptom (data state) | DB rows touched, S3 keys, JSONB shape |
| bot exit signal | `exit_code`, `reason`, `error_details`, gracefulLeave path |
| status transition log | full `meetings.data.status_transition[]` |
| concurrent collector signals | anomalies in meeting-api / collector logs |
| pre-classified mapping (optional) | PLATFORM's read against the 0.10.6 taxonomy |
| relation to existing fixes (optional) | which packs / commits this hits |

OSS will:

1. Assign `FM-NNN` (next sequential).
2. Pick the matching `status` from the vocabulary.
3. Cross-reference linked commits / packs / files.
4. File the entry under the new ID.
5. Update the index table.
6. Notify team-lead + PLATFORM in the same thread.

If OSS needs more data (e.g. specific JSONB fields, pod logs, image
pin), OSS replies with the request — no blind speculation. PLATFORM
re-pulls and the entry transitions through `pending-data` until full.

---

## FM-002 — bot exits with non-allowlisted `reason` bypass Pack J classifier

```yaml
id: FM-002
first_observed: 2026-04-28   # via 11161; broader class predates this date
platform: "*"                # any — exit reason is platform-independent
status: open
class: structural             # not a single user-visible failure; affects every exit path through the else branch
linked_classifier_path: services/meeting-api/meeting_api/callbacks.py:262-326
related_entries:
  - FM-001     # 11161 is one instance of this class
prod_aggregate_7d:            # PLATFORM pull, 2026-04-28
  status_failed_completion_reason_NULL: 182
  status_completed_completion_reason_stopped: 331
  status_completed_completion_reason_awaiting_admission_timeout: 72
  status_completed_completion_reason_max_bot_time_exceeded: 9
  status_completed_completion_reason_evicted: 3
candidate_fixes:
  - title: extend allowlist
    summary: add `post_join_setup_error` (and other non-allowlisted bot reasons) to the Pack J allowlist at callbacks.py:264-270
    risk: low — keeps existing semantics, just routes more exits through the classifier
  - title: route any active→exit through Pack J unconditionally
    summary: drop the reason-allowlist gate; let `_classify_stopped_exit` handle every exit from active state, deriving `completion_reason` from payload.reason or defaulting to BOT_CRASHED_IN_CALL
    risk: medium — broader behavior change; needs verification against the COMPLETED routings (332 stopped + 72 awaiting_admission_timeout etc.)
deferred_fixes:
  - target: v0.10.6
    pack: Σ
    summary: Pack Σ's "single central outcome classifier called from every terminal write site" is the principled fix — closes the structural gap, not just this one allowlist
```

### Symptom (data-side)

Meeting exits via `bot_exit_callback` with `payload.reason` not in the
allowlist `{self_initiated_leave, evicted, left_alone, removed_by_host, meeting_ended_by_host}`
and `payload.completion_reason` not set. The handler at
`callbacks.py:311-326` writes:

- `status = FAILED`
- `failure_stage` from payload (or default `ACTIVE` if absent)
- `error_details` if payload provided it
- `last_error` JSONB blob

But **does NOT call `_classify_stopped_exit`**, and therefore **does NOT
write `completion_reason`**. The meeting row ends up `status=failed,
completion_reason=NULL`.

### Why this matters

- Engineering can't distinguish "bot crashed in call (197 segments
  delivered)" from "bot crashed pre-admission (0 segments)" by querying
  on `completion_reason` — both bucket as NULL.
- Product can't read funnel cause attribution; the most common terminal
  failure class today (182 / 7d) is unattributed.
- v0.10.6 Pack Σ taxonomy work assumes every terminal write goes through
  the central classifier. This exit path is one of the parallel-write
  sites the README's WHY section calls out: *"every miss in v0.10.5 was a
  parallel-write-site bug."*

### Why this happens

`callbacks.py:262-271` gates the Pack J branch on:

```
meeting.status == ACTIVE
AND (payload.completion_reason set OR payload.reason in {self_initiated_leave, evicted, left_alone, removed_by_host, meeting_ended_by_host})
```

The allowlist was scoped to known "normal completion" reasons during
the v0.10.5 develop iter that added Pack J. Bot-side `gracefulLeaveFunction`
calls report a wider set of `reason` strings — including
`post_join_setup_error` (FM-001), `admission_timeout`,
`admission_rejected_by_admin`, `stopped_requested_pre_admission`,
`admission_false_positive`, `removed_by_admin`, `left_alone_timeout`,
`startup_alone_timeout`, `normal_completion`, `missing_meeting_url`,
`join_meeting_error` — see `meetingFlow.ts:55-254` for the full set.
Of these, only `removed_by_admin` is allowlisted via the alias
`removed_by_host`; the rest fall through to the `else` branch
unclassified.

### Repro

Trigger any bot exit path that doesn't set `completion_reason` and
reports a `reason` outside the allowlist. Easiest: induce a Playwright
exception inside `startGoogleRecording` (FM-001 path).

Synthetic via the Pack X test rig: post a `bot_exit_callback` with
`payload.reason="custom_failure_xyz"`, `payload.completion_reason=null`,
and observe the meeting row lands `failed` / `completion_reason=null`.

### Why FM-001's user-visible damage IS still fixed by 0.10.5

FM-002 is a **classifier-coverage** gap. The user-visible damage (orphan
recording in dashboard) is fixed by Pack E.1.a (`fa88c2c`) regardless of
how the meeting row's `completion_reason` ends up — because `media_files`
is written on every chunk upload, not at the terminal classifier. So
FM-001's "user has stuff" Frame 2 walkthrough (in the team-lead Q&A)
still holds: post-0.10.5 promote, the user gets their recording. FM-002
is the engineering signal that's still missing.

### Recommended next step

Lowest-risk patch is the allowlist extension. Highest-leverage fix is
Pack Σ's single central classifier (v0.10.6). The "extend v0.10.5
scope" decision answer hinges in part on this — patching the allowlist
is a 2-line change but it papers over the structural problem that's
the real reason 182 rows / 7d sit in NULL. The structural fix only
makes sense as part of Pack Σ.

---

## FM-003 — `failure_stage` from bot payload doesn't reflect actual stage

```yaml
id: FM-003
first_observed: 2026-04-28   # via 11161; 68 instances in 7d (PLATFORM aggregate)
platform: "*"
status: open
class: write-side mislabel
linked_callback_path: services/meeting-api/meeting_api/callbacks.py:312     # provided_stage = payload.failure_stage or MeetingFailureStage.ACTIVE
linked_bot_path: services/vexa-bot/core/src/utils.ts                         # callJoiningCallback / callStartupCallback / callNeedsHumanHelpCallback set the stage
related_entries:
  - FM-001     # 11161 is one instance
linked_partial_fixes:
  - commit: 3b54143   # dashboard TS narrowing — read-side tolerance
  - commit: f0e618a   # Pack R failure_stage allowlist — read-side
  - commit: c6937db   # MeetingResponse tolerates legacy invalid failure_stage — read-side
prod_aggregate_7d_failure_stage_x_reached_active:    # PLATFORM 2026-04-28
  failure_stage_joining_reached_active: 68     # mislabeled (FM-003 class)
  failure_stage_active_never_active: 59        # likely FM-003-symmetric class
  failure_stage_active_reached_active: 28      # correct
  failure_stage_NULL_never_active: 15
  failure_stage_NULL_reached_active: 12
deferred_fixes:
  - target: v0.10.6
    pack: Σ
    summary: README line 427 already names this: "`failure_stage: active` paints over reality — replace default with `unknown`". Pack Σ's `data.audio_confirmed_at` flag closes a related drift; this entry is the symmetric one (write-side mislabel from bot payload).
candidate_fixes:
  - title: derive failure_stage from current meeting.status at write time
    summary: ignore payload.failure_stage; compute from meeting.status (e.g. status=ACTIVE → failure_stage=ACTIVE; status=AWAITING_ADMISSION → failure_stage=ADMISSION; etc.)
    risk: low — single write-site change at callbacks.py:312
  - title: bot-side fix — gracefulLeaveFunction sets failure_stage from current state
    summary: bot tracks its own state; reports the actual current stage at exit. Symmetric to server-side but spread across multiple call sites.
    risk: medium — multiple bot call sites; per-platform variation
```

### Symptom

Meeting reaches `active` (status_transition shows it). Bot crashes /
exits abnormally. `failure_stage` written to the row reads `joining` —
which is the stage the bot's `gracefulLeave` payload reports, NOT the
stage the meeting was actually in.

For 11161: meeting was active for 30 min (197 segments delivered). Row
ends up with `failure_stage="joining"`. Dashboard renders "Failure
stage: joining" alongside a 30-minute transcript.

### Symmetric variant

The same write-site at `callbacks.py:312` defaults to
`MeetingFailureStage.ACTIVE` when payload omits `failure_stage`. So a
meeting that never reached active can end up `failure_stage="active"` if
the bot fired the exit callback without an explicit stage. PLATFORM's
7d aggregate shows 59 such rows. README line 427 calls this out:
*"`failure_stage: active` paints over reality"*. FM-003 covers both the
mislabeled-from-payload variant (joining for an active meeting, 68 rows)
and the defaulted-to-ACTIVE variant (active for a never-active meeting,
59 rows) — same root cause: `failure_stage` is sourced from a place
that doesn't know the meeting's actual state.

### Repro

Trigger any bot exit from active state where the bot's payload reports
a `failure_stage` other than `active`. The bot's `gracefulLeaveFunction`
in the `post_join_setup_error` catch path doesn't update its own stage
tracker before sending — so it reports whatever stage was set when the
gracefulLeave wrapper was instantiated, which for the recording.ts
crash path is "joining" (the stage at which `runMeetingFlow` first
called `gracefulLeaveFunction`).

### Why partial-fixes are read-side only

`3b54143`, `f0e618a`, `c6937db` make the dashboard tolerant of mislabeled
`failure_stage` values (allowlist + TS narrowing + legacy-tolerant read
serialization). They prevent crashes / type errors on the read path.
None of them correct the value at write time — the row still stores the
wrong stage.

### Recommended next step

Server-side derivation at `callbacks.py:312` is the lowest-risk patch:
ignore `payload.failure_stage`, compute from `meeting.status`. Single
write site, no bot-side change, no migration. Worth pairing with FM-002's
allowlist extension as a small "pre-Pack-Σ honesty patch" in v0.10.5
develop iter — both are 2-line changes that materially reduce the lying
on user dashboards without waiting for the full taxonomy redesign.

Pack Σ's principled fix is the same single-central-classifier work that
addresses FM-002.

---

## FM-004 — gmeet end-of-meeting nav crash signature catalog

> **Update 2026-04-28:** the "trigger unknown" framing in this entry is
> obsolete. PLATFORM repro evidence (n=23 sampled) identified the
> trigger as **gmeet's end-of-meeting page navigation** — not a random
> mid-meeting destruction. Bot-side classification fix moved into FM-001
> scope alongside Pack Σ. This entry is kept as the **detection-signature
> catalog** for retrospective grepping (`error_details ILIKE '%Execution context was destroyed%'`)
> and as the cross-platform tracker if the same signature appears outside
> gmeet (1 msteams instance observed in 14d — worth watching).
>
> The "candidate fixes" in the YAML below pre-dated the trigger
> identification. The current fix path is in **FM-001 § Bot-side
> hardening — reframed after trigger identification**.

```yaml
id: FM-004
slug: gmeet-end-of-meeting-nav-crash-signature
first_observed: 2026-04-19   # onset; rate was 0% before
platform: google_meet
status: superseded-by-FM-001  # bot-side classification fix tracked under FM-001 scope; this entry retained for detection-signature reuse
class: bot-side crash signature (cross-platform tracker)
related_entries:
  - FM-001                    # all 84 gmeet instances are FM-001 trigger; 1 msteams instance is the cross-platform signal worth tracking
detection_query: |
  SELECT meeting_id FROM meetings
  WHERE platform = 'google_meet'
    AND data->>'error_details' ILIKE '%Execution context was destroyed%'
stack_signature:
  - "at startGoogleRecording (recording.js:102)"
  - "at runMeetingFlow (meetingFlow.js:178)"
prod_aggregate_14d:           # PLATFORM 2026-04-28
  google_meet_instances: 84
  msteams_instances: 1        # signal that the issue isn't strictly gmeet-DOM-specific
  onset: 2026-04-19           # rate was 0% before this date
  current_rate: 0.258         # 8 / 31 gmeet sessions today (25.8%)
  rate_floor_since_onset: 0.07
  rate_ceiling_since_onset: 0.32
duration_to_crash:
  mean_seconds: 798           # ~13.3 min
  longest_seconds: 3426       # 57.1 min
  mid_meeting_fraction: 0.88  # 88% of crashes happen during, not at start/end
deterministic_repro:
  evidence: user 1970 retried same gmeet URL 8x → same crash 8x
  inference: trigger is in the meeting page itself, not random network noise
impact:
  pre_pack_e1a:
    transcript_segments_orphaned_14d: 2640
    distinct_users_14d: 24
  post_pack_e1a:
    user_visible_recording: recovered
    bot_session_truncated: yes  # bot still exits at the crash point; user loses post-crash audio
unknown:
  what_navigation_fires: |
    Candidates (no evidence to distinguish yet):
      - gmeet idle-redirect (server-driven)
      - captcha challenge insertion
      - post-end-of-meeting nav
      - user-clicked picture-in-picture / captions-pane / breakout-room
      - Google A/B feature flag flip
      - chrome-side renderer process restart
diagnostic_gap: |
  Bot pod is deleted post-failure; no in-page log of what gmeet
  did at T-0. Recommendation (one-line, scope v0.10.6):
    page.on('framenavigated', frame => log(`framenavigated url=${frame.url()} ts=${Date.now()}`))
  installed in startGoogleRecording before the inner page.evaluate.
  Captures URL+timestamp at the moment of nav, BEFORE the execution
  context is destroyed, so the next occurrence has evidence.
candidate_fixes:
  - title: framenavigated diagnostic listener
    summary: not a fix — captures the nav URL so next instance reveals the trigger
    risk: trivial; one line; should land regardless of which fix path
  - title: split monolithic page.evaluate into smaller round-trips
    summary: smaller evaluations re-acquire context per call; long-running in-page recorder rewrite
    risk: medium; touches the recording pipeline
  - title: page.on('framenavigated') auto-restart of in-page recorder
    summary: detect the nav, re-inject the recorder, replay missed audio (if any survived in MediaRecorder buffer)
    risk: medium; race conditions around in-page state
  - title: CDP-driven external capture invariant to in-page nav
    summary: capture audio via CDP browser-level handle instead of in-page MediaRecorder; survives page nav entirely
    risk: high; substantial rearchitecture; v0.11.x
fix_horizon:
  v0.10.6: framenavigated diagnostic listener (always); reactive fix selection after one or two occurrences with evidence
  v0.11.x: CDP-driven external capture (if the diagnostic shows the nav can't be prevented bot-side)
note_on_msteams_instance:
  Single msteams instance with the same stack signature suggests this is
  not strictly gmeet-DOM-specific. Could indicate a Playwright / Chrome
  issue triggered by ANY page nav under load. Worth widening the detection
  query in v0.10.6 to all platforms.
```

### Why this is registered separately from FM-001

FM-001 captures the **user-visible failure for meeting 11161** and the
broader 14-day class with 14 prod observations. FM-004 captures the
**underlying bot-side crash signature** that drives FM-001 — the
Playwright `Execution context was destroyed` exception originating at
`recording.ts:102` (`startGoogleRecording`). They're logically distinct
because:

- FM-001's user-visible damage (orphan recording) was a **meeting-api
  bug** — fixed in 0.10.5 via Pack E.1.a.
- FM-004's underlying crash is a **bot bug** (or, more precisely, a
  bot's inability to survive an external trigger). Not fixed in 0.10.5.

Even after Pack E.1.a + Pack Σ ship, FM-004 will still be observed as
"bot session ended early due to gmeet navigation" — the recording will
be saved (Pack E.1.a) and the meeting will be classified as
`success.recorded_then_bot_crashed` (Pack Σ), but the user still loses
post-crash audio because the bot exits.

### Detection signature (post-Pack-E.1.a)

```
status:           failed (or success.recorded_then_bot_crashed under Pack Σ)
data.error_details ILIKE '%Execution context was destroyed%'
data.recordings[].status: completed (Pack E.1.a finalized via gracefulLeave)
data.recordings[].media_files: non-empty   ← the difference vs pre-Pack-E.1.a
```

Detection queries should join on `error_details ILIKE` rather than on
`status` alone, since post-Pack-Σ this row will appear in the success
bucket. Keeping the stack-signature match at the data-state level keeps
the FM-004 cohort traceable across taxonomy changes.

### Onset 2026-04-19

Sharp from 0% → sustained 7-32% in a 14-day window. Three hypotheses:

1. **Gmeet server-side change** — Google rolled out a feature or A/B
   test that adds a navigation we don't handle. Possible at this scale.
2. **Our own change** — code search shows no bot-side or
   meeting-api-side change to the recording path on 2026-04-19. Last
   gmeet-recording-relevant commits are well outside this window.
3. **Our detection improved** — an instrumentation change started
   capturing what was previously silent. Less likely; the
   `Execution context was destroyed` string is uniformly thrown by
   Playwright and would have been captured before.

Hypothesis 1 is the most plausible without further evidence. The
framenavigated listener (above) would discriminate.

### Recommended next step

Land the framenavigated diagnostic listener in v0.10.6 develop iter,
build, deploy. Wait for one or two prod instances to surface the
captured URL. From there, the fix path becomes clear:
- Same-origin gmeet redirect → likely page.on('framenavigated')
  auto-restart.
- Captcha / unfamiliar UI → escalate to `needs_help` (Pack Σ already
  has this state).
- A user-clicked feature → bot can detect from URL pattern and
  pre-emptively pause/resume.
- Cross-origin nav → CDP-driven external capture is the long-term fix.

Not committing to a fix path until we have the diagnostic evidence.

---

## FM-005 — Option C cosmetic carry-forward (v0.10.6)

```yaml
id: FM-005
first_observed: 2026-04-28               # ARCH-2 code review of commit 0243737
platform: n/a                            # internal code-quality only
status: open
target: v0.10.6
parent: FM-001 / FM-002 / FM-003 (Option C cosmetic carry-forward)
class: cosmetic / hardening
severity: cosmetic                        # not a bug, no user impact, no validate impact
linked_fixes: []
prod_observations: 0
filed_by: ARCH-2 (2026-04-28 review of Option C ship)
linked_callbacks_path: services/meeting-api/meeting_api/callbacks.py
deferred_fixes:
  - target: v0.10.6
    pack: Σ
    summary: bundle into Pack Σ where the classifier dict + failure-stage taxonomy are already being restructured
```

Two cosmetic items raised by ARCH-2 during the Option C ship review.
Neither blocks deploy. Both right-sized to land inside Pack Σ where the
shape is changing anyway.

### Item 1 — `_BOT_REASON_TO_COMPLETION` defined inline

`services/meeting-api/meeting_api/callbacks.py` ~lines 308-325 inside
the else-branch of `bot_exit_callback`. The 14-entry dict gets rebuilt
on every callback. Cosmetic / minor perf — not measurable in practice.

**Fix shape:** lift to module-level constant near
`_failure_stage_from_status` (~line 52):

```python
from types import MappingProxyType

_BOT_REASON_TO_COMPLETION: Mapping[str, MeetingCompletionReason] = MappingProxyType({
    "self_initiated_leave": MeetingCompletionReason.STOPPED,
    # ...etc
})
```

Two lines moved. No behavioural delta.

**Why not now:** pure refactor; doesn't earn re-validate. Pack Σ
restructures the classifier anyway — the dict either becomes a method on
the new outcome resolver or moves into a per-platform translator table.
Land alongside that.

### Item 2 — `_failure_stage_from_status` silent default

`services/meeting-api/meeting_api/callbacks.py:52-66`. The helper ends
in `.get(status, MeetingFailureStage.ACTIVE)` — silently swallows any
`MeetingStatus` value not in the dict. Same shape as the FM-002 problem
the `unknown_bot_reason` canary is meant to prevent: a future enum
addition (e.g. a new transitional state in v0.10.6) routes to ACTIVE
without observability.

**Fix shape:** mirror the FM-002 canary pattern. WARN log on the default
branch + stash `transition_metadata['unknown_failure_status'] = status`
at the call site. DATA gets a second canary watch.

**Why not now:** needs the canary plumbing (caller passes back the
unknown-status flag), not a one-liner. Pack Σ rebuilds the failure-stage
taxonomy regardless — fits there.

### Why this is filed at all

ARCH-2's principle: every cosmetic deferral is a tracked entry, not a
mental note. Two cosmetic smells that ship as-is in v0.10.5 → file FM-005,
target v0.10.6, link the parent fix. If they slip past Pack Σ they
become observable as their own entry rather than rotting silently.

---

## FM-006 — full Recall-parity `bot_lifecycle` envelope

```yaml
id: FM-006
slug: bot-lifecycle-envelope
first_observed: 2026-04-28        # internal — ARCH-2 north-star check
platform: n/a                     # internal API surface / contract shape
status: open
target: v0.10.6
parent: Pack Σ
class: design / deferred
severity: design                   # not a bug; v0.10.5 ships 80% via additive promote
linked_fixes: []
prod_observations: 0
filed_by: ARCH-2 (2026-04-28 north-star check, 13:30); routed to OSS-2 post-restart
linked_callbacks_path: services/meeting-api/meeting_api/callbacks.py
linked_schemas_path:   services/meeting-api/meeting_api/schemas.py
linked_webhook_path:   services/meeting-api/meeting_api/webhooks.py
sequencing:
  blocks_on: FM-007                # vocab package is the single source of exit-reason strings
  blocks: FM-008                  # 4-bucket outcome taxonomy reads from the envelope
deferred_fixes:
  - target: v0.10.6
    pack: Σ
    summary: implement the typed envelope after FM-007 keystone lands
```

### What this entry captures

The full Recall-parity envelope on `MeetingResponse` and webhook payload. Shape:

```python
bot_lifecycle: {
  joined_at: datetime | None,
  left_at: datetime | None,
  recorded_seconds: int | None,
  segment_count: int | None,
  exit_reason: str,             # from FM-007 vocab
  failed_at_stage: str | None,  # from MeetingFailureStage
  status_path: list[str],       # ordered status_transition slugs
}
```

### Why deferred (v0.10.5 ships 80%)

Mid-flight v0.10.5 additive promote (`daa5cc5`) ships typed `completion_reason` + `failure_stage` to the top level — closes ~80% of Recall-parity (the two fields most-frequently consumed by displaced customers). The full envelope adds joined/left timestamps, transcript stats, and the ordered status path — useful for richer integrations but not on the v0.10.5 critical path.

Implementation blocks on FM-007: `exit_reason` and `failed_at_stage` should consume the vocab package's typed enums, not strings. Building the envelope before vocab lands re-creates the parallel-enum drift FM-007 keystone is approved to kill.

### Cross-references

- Architecture room: `/home/dima/dev/human/architecture.md` (Pack Σ priority stack item 2)
- v0.10.5 partial closure: commit `daa5cc5` (additive promote of `completion_reason` + `failure_stage`)
- Release room: `/home/dima/dev/human/release-0.10.5.md` (NORTH STAR §1, Recall-parity row marked ✅ for additive-promote scope)

---

## FM-007 — shared `bot-lifecycle-vocab` package (Pack Σ keystone)

```yaml
id: FM-007
slug: bot-lifecycle-vocab-package
first_observed: 2026-04-28        # team-lead approval
platform: n/a                     # internal package / contract shape
status: open
target: v0.10.6
parent: Pack Σ                    # keystone — head of the priority stack
class: design / keystone
severity: design                   # not a bug; structurally kills FM-002 family
linked_fixes: []
prod_observations: 0
filed_by: team-lead approved 2026-04-28 ~17:50; routed to OSS-2 post-restart
linked_consumer_paths:
  - services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:55-254  # bot exit-reason emission
  - services/meeting-api/meeting_api/callbacks.py:52                   # _classify_stopped_exit
  - services/meeting-api/meeting_api/schemas.py                        # MeetingCompletionReason / MeetingFailureStage
  - dashboard composite view (TBD)                                     # SDK type-gen target
sequencing:
  blocks: [FM-006, FM-008, "FM-005 items 1+2", cross-repo JSONB audit]  # entire Pack Σ stack waits on this
estimated_effort: ~1 week (per architecture room)
implementation_questions:
  - location: where does the package live? (shared monorepo path vs published artifact)
  - language: Python source-of-truth + TS code-gen, or vice versa?
  - sdk_codegen_path: which generator(s) consume it?
deferred_fixes:
  - target: v0.10.6
    pack: Σ keystone
    summary: stand up the package post-0.10.5-ship; OSS-2 owner per 2026-04-28 ~18:30 routing
```

### What this entry captures

Single source of truth for:

1. **Bot exit-reason strings** — the catalog currently scattered across `vexa-bot/core/src/platforms/shared/meetingFlow.ts:55-254` (~14 strings) and aliased into the meeting-api `_classify_stopped_exit` allowlist (`callbacks.py:262-271`).
2. **Completion-reason mapping** — bot reason → `MeetingCompletionReason` enum value, consumed by classifier.
3. **Human-readable descriptions** — what each reason means, surfaced to dashboard / SDK consumers.

Imported by: bot (emits), meeting-api (classifies), SDK type-gen (consumes for typed customer integrations), dashboard (renders).

### Why this is the keystone

Kills the **two-enums-secretly-meant-to-match** antipattern at its source. FM-002 (allowlist bypass) was the most-recent prod symptom — bot emitted `post_join_setup_error` and the meeting-api classifier didn't recognize it, dropping `completion_reason` to NULL on 182 rows / 7d. The v0.10.5 fix (Option C central classifier) papers over by allowlisting; the structural fix is one shared vocabulary that compile-fails when out-of-sync.

The unknown_bot_reason canary (decided 2026-04-28) is the bridge: until vocab lands, unknown reasons stash to `transition_metadata.unknown_bot_reason` and DATA mints catalog updates. Post-vocab, the catalog IS the contract; canary becomes a regression detector.

### Why this blocks the entire Pack Σ stack

- **FM-006** (envelope) consumes `exit_reason` typed from vocab — pre-vocab build = strings = drift.
- **FM-008** (4-bucket outcome) reads cause from vocab — pre-vocab build = parallel enum at outcome layer (the exact pattern FM-007 kills).
- **FM-005 item 1** (inline-dict refactor) — the dict IS the vocab; refactor lands as a vocab consumer.
- **FM-005 item 2** (MeetingFailureStage canary) — same canary primitive as bot-reason; second instance of the pattern, lands together.
- **Cross-repo JSONB audit** — uses vocab as a precedent for "shared contract via shared package, not shared JSONB shape".

### Implementation questions (for OSS-2 post-ship scope)

- **Location.** `packages/vexa-lifecycle-vocab/` in the OSS monorepo? Or a published artifact (`@vexa/lifecycle-vocab` on npm + PyPI)?
- **Language.** Python source-of-truth (closest to the classifier) with TS code-gen for bot + dashboard? Or YAML/JSON source-of-truth with both Python + TS code-gen?
- **SDK type-gen path.** Currently the SDK is hand-written; vocab as code-gen input is the right shape but needs the SDK type-gen pipeline to exist.

These are scoping decisions, not implementation blockers. OSS-2 picks the answer post-0.10.5-ship as part of FM-007 implementation kickoff.

### Cross-references

- Architecture room: `/home/dima/dev/human/architecture.md` — Pack Σ priority stack item 1 (keystone), Decisions log 2026-04-28 entry 3
- Recurring patterns observed: "two enums secretly meant to match" — first named instance
- v0.10.5 patch that papers over: commit `0243737` (Option C central classifier with allowlist)
- Canary primitive: decision 2026-04-28 in architecture room
- Downstream blocked: FM-005 (items 1+2), FM-006, FM-008

---

## FM-008 — bot-lifecycle outcome-taxonomy redesign

```yaml
id: FM-008
slug: bot-lifecycle-outcome-taxonomy-redesign
first_observed: 2026-04-29   # internal redesign session, not a prod symptom
platform: n/a                # internal taxonomy / data shape
status: open
target: v0.10.6
parent: FM-006 (bot_lifecycle envelope) / FM-007 (bot-lifecycle-vocab keystone)
class: design / deferred
severity: design              # not a bug; v0.10.5 ships without it
linked_fixes: []
prod_observations: 0
filed_by: ARCH-2 directive (2026-04-29 ~13:00) after OSS-2 + team-lead redesign session
linked_callbacks_path: services/meeting-api/meeting_api/callbacks.py
linked_readme: features/bot-lifecycle/README.md   # rewritten ~463 lines, uncommitted on release/260427
linked_groom_seed: tests3/releases/260427/scope.yaml (Pack Σ entry, commit 8154359)
sequencing:
  blocks_on: FM-007            # vocab IS the substrate; without it FM-008 re-creates parallel-enum drift at outcome layer
  blocks: FM-006              # full envelope depends on outcome shape
deferred_fixes:
  - target: v0.10.6
    pack: Σ
    summary: implement after FM-007 (bot-lifecycle-vocab) keystone lands
```

### What this entry captures

The 4 design decisions that emerged from the 2026-04-29 09:00-09:45 MSK redesign session (OSS-2 + team-lead). Each is a v0.10.5-out-of-scope decision parked for post-FM-007 implementation in v0.10.6 / Pack Σ.

#### 1. Four-bucket outcome model

`success` / `user_aborted` / `environment_blocked` / `software_fault`, organized into normal vs abnormal:

- **NORMAL** (no attention required): `success` ∪ `user_aborted` — maps to `v1.status=completed`
- **ABNORMAL** (something needs attention): `environment_blocked` ∪ `software_fault` — maps to `v1.status=failed`

~30 sub-causes, agency-prefixed: `user_*`, `host_*`, `requires_*`, `navigation_*`, `bot_self_*`, `bot_*`, `ux_gap_*`. Engineering's bug-rate query filters to `outcome=software_fault`; product funnel groups by `outcome=user_aborted` cause distribution; sales/docs reads `outcome=environment_blocked` patterns.

#### 2. `data.audio_confirmed_at` flag

Encodes the "bot reached the meeting DOM ≠ bot is actually capturing audio" invariant without splitting `active` into two lifecycle states. Set by bot (or collector on first segment landing). Classifier reads this to distinguish `success.recorded_*` from `software_fault.audio_pipeline_failed`.

**Zero contract break for existing webhook consumers** — `meeting.status` semantics unchanged; new field is additive. This is the architectural alternative to renaming `active` → `in_call_not_recording`/`in_call_recording`, which was rejected in the same session as not paying back the migration cost.

#### 3. `needs_human_help` → `needs_help` rename

Clean rename, no alias bridge. State is ~3wk old (introduced 2026-04-05 in commit `9b8ba83`); no external webhook consumers depend on the string in production. Semantic is broader than human-only — the AI assist-agent path planned for v0.10.7+ uses the same lifecycle state with same VNC affordance.

Code change ~5 LOC: enum value, callback function name (`callNeedsHumanHelpCallback` → `callNeedsHelpCallback`), DB string. Doc note in v0.10.6 release notes.

#### 4. `environment_blocked` sub-classification policy

Spans host-action / platform-requirement / navigation-blocked / infra spectrum:

- **host actions** (clearly host's choice): `host_did_not_admit`, `host_rejected_admission`, `host_kicked_pre_recording`, `host_locked_meeting`, `meeting_full`, `meeting_not_started_yet`, `meeting_already_ended`
- **platform-side prerequisites** (meeting requires X): `requires_authentication` (collapses signin/SSO/2FA in practice — indistinguishable to bot), `requires_registration`, `requires_captcha`, `org_restricted`
- **navigation gap** (bot reached UI but couldn't pass it): `navigation_blocked_unfamiliar_ui` (catches LFX/AWS portal stuck without resolution), `navigation_blocked_button_not_found`
- **infrastructure**: `network_dropped_connection`, `platform_server_error`, `platform_idle_kick`, `platform_max_duration_kick`

Some causes are technically indistinguishable in practice (`requires_authentication` vs `navigation_blocked_unfamiliar_ui` both look like "we hit a sign-in dialog and didn't proceed"). Classifier policy: pick most-specific detectable cause (literal text indicators, known SSO redirect patterns), fall back to generic when ambiguous. Either way the user sees the same actionable message and retry/contact-host affordance.

#### 5. Six-surface delivery framing

Audience × outcome matrix mapped:

| audience | reads | how |
|---|---|---|
| user (dashboard) | `data.outcome` + `data.cause` | 5-class visual treatment (✅⚪⚠🔵🔴), plain-English message, action button |
| product (analytics) | JSONB indices on `outcome` + `cause` | funnel queries; cohort by user/platform |
| engineering (observability) | structured log lines + audit trail | per-meeting debug + cluster queries |
| devops (monitoring) | OSS-emitted log shape, PLATFORM-owned scrape pipeline | rate-of-`software_fault` page alerts; rate-of-`automation_gap_unresolved` slack |
| API (integrators) | webhook v1 (additive only — narrowed status semantic) + webhook v2 (opt-in, ships outcome+cause+audio_confirmed_at) | `X-Vexa-Webhook-Version` header; `user.data.webhook_version` opt-in |
| docs (release-notes) | migration guide for v1→v2 | one-page diff; recall translator availability for migrations |

OSS does NOT ship `prometheus_client` or `/metrics` endpoint. Structured log lines are the contract; PLATFORM owns the scrape pipeline.

### Why deferred (not extending v0.10.5 scope)

ARCH-2 verdict 2026-04-29 ~10:30 — **SHIP ADDITIVE-ONLY** in v0.10.5. Reasons:

1. **Sequencing.** Outcome-taxonomy without `bot-lifecycle-vocab` (FM-007 keystone) re-creates "two enums secretly meant to match" at the outcome-bucket layer; vocab IS the substrate; build substrate first. Today's 4-bucket model + ~30 sub-causes is exactly the new-enum-aligned-by-mapping shape FM-007 was approved to kill.
2. **North-star fit.** Additive-promote already closes 80% of Recall-parity (approved 2026-04-28 scope); full envelope was already deferred to FM-006. FM-008 adds *more* shape on top of the same data-still-lies-in-JSONB gap, not closing it.
3. **Stage hygiene.** Stage is `deploy`; extending = re-enter develop = stack a second stage-machine override on yesterday's audit trail. Each override is debt; pay it down by shipping.
4. **"Ship today" violation.** North star said "today"; +3-4d compounds delay without changing user-visible value (FM-001/002/003 closed + 80% surface honesty IS the value).

### Open decisions parked under this entry

- **v1 webhook semantic narrowing.** Three options surfaced during the redesign session:
  - **A** (outcome-derived): `v1.status=completed` if `outcome ∈ {success, user_aborted}` else `failed`. Behavioral change vs v0.10.4 — failed-bucket narrows; existing alerts see fewer fires (improvement, not regression).
  - **B** (Pack-J-only): keep current behavior; ship outcome/cause as additive fields only.
  - **C** (configurable): per-user `legacy_failed_semantic: true` flag → v1 emits Pack-J semantic; default false → outcome-derived.
  - OSS-2 recommendation: **A** with release-note callout. Revert to **C** if specific customers push back. Decision parks under SHIP-ADDITIVE-ONLY verdict; revisits when the redesign actually lands.

- **`outcome_class` as a field.** Resolved during session: **DROP** (redundant with v1.status binary under option A). Ship 3 new fields (`outcome` / `cause` / `audio_confirmed_at`), not 4.

- **Bulk lifecycle state renames** (`awaiting_admission` → `in_waiting_room`, `active` → split, `stopping` → `call_ended`, `completed` → `done`, `failed` → `fatal`). Resolved during session: **DROP**. Migration cost for us + every webhook consumer doesn't pay back; recall translator handles name mapping at the boundary either way.

### How this entry will close

Three-phase implementation in v0.10.6:

1. **Phase 1** (one cycle): schema + central classifier + audio-confirmation gate + backfill script + webhook v2 (opt-in feature flag) + recall translator skeleton.
2. **Phase 2** (next cycle): webhook v2 default; v1 deprecated with timeline.
3. **Phase 3** (third cycle): old taxonomy retired.

Pre-condition: FM-007 (`bot-lifecycle-vocab`) keystone lands first. Without it, the outcome enum gets pinned in two places (meeting-api + bot) and drifts. With it, both consume the shared package; drift is a build error.

### Cross-references

- README rewrite: `features/bot-lifecycle/README.md` (~463 lines, uncommitted on `release/260427`, captures WHY/WHAT/HOW framing for the redesign).
- Parent groom seed: `tests3/releases/260427/scope.yaml` Pack Σ entry (commit `8154359`).
- Architecture room: `/home/dima/dev/human/architecture.md` (Pack Σ sequencing — `bot-lifecycle-vocab` keystone → envelope → MeetingFailureStage canary → inline-dict refactor → cross-repo JSONB audit).
- Release room: `/home/dima/dev/human/release-0.10.5.md` (Decisions log entries dated 2026-04-29; LOG entries 09:00-09:45).
- Recall.ai mapping: future translator at `services/meeting-api/meeting_api/translators/recall.py` — static lookup table; agency lift from sub_code names → vexa outcome.

---

## Cross-reference

- Lifecycle README: `features/bot-lifecycle/README.md` (Failure modes table at lines 417-435 is historical / lessons; this file supersedes for tracked entries).
- Outcome classifier: `services/meeting-api/meeting_api/callbacks.py:52` (`_classify_stopped_exit`).
- Bot recording entry point: `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:124` (`page.evaluate` that throws on context destruction).
- Bot meeting-flow / gracefulLeave: `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:196-256`.
- gmeet recording entry: `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:18` (`startGoogleRecording` — FM-004 throw site).
- Pack Σ groom seed: commit `8154359` (v0.10.6 lifecycle taxonomy redesign).
