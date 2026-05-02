---
services:
- meeting-api
- runtime-api
- vexa-bot
---

# Bot Lifecycle

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 90%**

**Current state.** v0.10.5 is **not shipped**. The release branch (`release/260427`) is in `develop` stage, debugging real bugs hit in live testing. The lifecycle work documented here is **the debugging** — we surfaced enough silent-classification failures ("default to failed" is dishonest, `active` is overloaded, multiple write paths drift apart) that the right fix isn't another patch; it's stepping back to decide what the lifecycle should actually MEAN before continuing. The shipped release in production is the previous one (`:latest`).

Status legend used throughout:

| marker | meaning |
|---|---|
| 🟢 | code on `release/260427`, in flight, NOT yet shipped to users |
| 🟡 | design only — being decided NOW; goes into v0.10.6 (or v0.10.5 itself if we extend scope) |
| 🔵 | direction noted; later release |

---

## WHY

A meeting bot has to deliver transcripts under conditions we don't fully control: third-party meeting platforms whose UIs change, hosts who admit/reject/kick, networks that flap, and our own automation that doesn't yet handle every variation. The lifecycle has to model that reality without lying to anyone reading it.

Three audiences read the lifecycle, asking different questions:

| audience | their question |
|---|---|
| **user** | what happened to my meeting? what should I do next? |
| **engineering** | is something broken in my code? |
| **product** | where do users drop off and why? |

Today's binary `completed | failed` conflates them — every bug we hit during v0.10.5 develop was a variant of "the data is lying to one of these audiences". The patches we shipped on the release branch (a routing-rule classifier, `audio_join_failed` escalation, stale-bot-container detection) close specific symptoms but inherit the same broken framing. **Live debugging is what surfaced this — and it's why we're redesigning the lifecycle before shipping v0.10.5, not after.**

Two non-obvious framings drive the redesign:

- **Default to specificity, never to a generic bucket.** When the classifier doesn't have a rule, that's its own named class (`classifier_gap`) — engineering's call to action, never user-facing. The "default to failed" rule was the wrong defensive move; the right one is "every meeting has a specific cause".
- **`needs_help` is part of the success path.** When automation hits an unfamiliar UI (LFX portal, captcha, magic-link auth), the bot pauses, exposes the browser, and lets a human (or 🔵 LLM-driven assist-agent later) push it through. The bot's selector wait keeps polling — whoever clicks the right button, the lifecycle resumes. Meetings that pass through this state and produce transcripts land in `success`, not `software_fault`.

A future `recall_to_vexa` API translator falls out of this design as a static lookup table.

---

## WHAT — the model

### Outcomes (4 buckets × 2 classes) 🟡

```
┌─────────────────────────────────┬──────────────────────────────────┐
│        NORMAL outcomes          │         ABNORMAL outcomes         │
│   (no attention required)       │   (something needs attention)     │
├─────────────────────────────────┼──────────────────────────────────┤
│  success                        │  environment_blocked              │
│    user got value               │    bot reached the platform but   │
│                                 │    couldn't pass through its UI   │
│  user_aborted                   │    (host action, platform req.,   │
│    user's deliberate action     │    navigation gap, network)       │
│    (cancel, stop, bad input)    │                                   │
│                                 │  software_fault                   │
│                                 │    our code or infra broke        │
└─────────────────────────────────┴──────────────────────────────────┘
```

| outcome | remediation owner | example |
|---|---|---|
| `success` | nobody | recorded; user stopped happy; everyone left |
| `user_aborted` | **product/UX** (funnel signal) | wrong password; user changed mind |
| `environment_blocked` | **docs/sales + engineering** (per sub-cause) | host didn't admit; meeting requires SSO; bot couldn't navigate portal page |
| `software_fault` | **ENGINEERING** (clean bug rate) | our code crashed; audio pipeline broke |

Each terminal meeting also gets a specific `cause` (~30 named values).

`environment_blocked` covers a spectrum that's worth sub-classifying because the remediation differs — and because **some of these are technically indistinguishable in practice**: when the bot reaches a sign-in page, we usually can't tell whether the meeting truly *requires* SSO for everyone or whether our automation just doesn't know how to bypass it. We pick the most-specific cause we can detect; fall back to a generic when the signal is ambiguous.

```
environment_blocked
  ─── host action (clearly the host's choice) ───
  ├─ host_did_not_admit              waiting room timeout, no explicit Decline
  ├─ host_rejected_admission         host clicked Decline (explicit signal)
  ├─ host_kicked_pre_recording       eviction event before recording started
  ├─ host_locked_meeting             meeting was locked when bot tried to join
  ├─ meeting_full                    at capacity
  ├─ meeting_not_started_yet         bot joined too early
  ├─ meeting_already_ended           bot joined after end

  ─── platform-side prerequisite (the meeting requires X) ───
  ├─ requires_authentication         signin / SSO / 2FA — usually the same to us
  ├─ requires_registration
  ├─ requires_captcha                detected captcha widget
  ├─ org_restricted                  GMeet org-only, Zoom auth-only-participants

  ─── navigation gap (bot reached UI but couldn't pass it) ───
  ├─ navigation_blocked_unfamiliar_ui  LFX/AWS portal, magic-link, etc.
  │                                    (catches the case where the bot
  │                                     escalated to needs_help and
  │                                     timed out without resolution)
  ├─ navigation_blocked_button_not_found  selector miss on a known platform

  ─── infrastructure ───
  ├─ network_dropped_connection
  ├─ platform_server_error              platform-side 5xx
  ├─ platform_idle_kick                 platform's own idle detection
  └─ platform_max_duration_kick         Zoom 40min free tier
```

The line between `requires_authentication` and `navigation_blocked_unfamiliar_ui` is fuzzy — both look like "we hit a sign-in dialog and didn't proceed". The bot tries to detect explicit indicators (literal text "you must sign in to join", known SSO redirect patterns) before falling back to the generic navigation-gap class. Either way, the user sees the same actionable message ("this meeting requires sign-in we can't bypass") with a retry/contact-host affordance.

For `success`, `user_aborted`, and `software_fault` the sub-causes are simpler:

```
success
  └─ recorded_fully | recorded_then_user_stopped | recorded_then_left_alone
     | recorded_then_max_time | recorded_then_host_ended | recorded_then_host_kicked

user_aborted
  └─ cancelled_before_join | cancelled_quickly | wrong_password_provided
     | wrong_url_provided | invalid_request

software_fault
  └─ bot_crashed_pre_join | bot_crashed_in_call | audio_pipeline_failed
     | audio_join_dialog_failed | runtime_failed_to_launch | classifier_gap
     | unknown_error
```

(Catalog grows per data — start with ~30 covering observed prod, not the full recall.ai 80+. Catalog only what data justifies.)

Filtering examples:

```sql
-- Engineering bug rate (the only signal that pages someone)
WHERE outcome = 'software_fault'

-- Product funnel — where do users drop?
WHERE outcome = 'user_aborted' GROUP BY cause

-- Environment patterns — actionable for docs/sales
WHERE outcome = 'environment_blocked' GROUP BY cause

-- Headline KPI
SELECT count(*) FILTER (WHERE outcome='success')::float / count(*)
```

User-facing dashboard maps each outcome to a visual class: ✅ success, ⚪ user_aborted, ⚠ environment_blocked, 🔴 software_fault. Most meetings are NOT red — only true software faults get the angry treatment.

**Webhook v1 contract** (back-compat preserved): `v1.completed = success ∪ user_aborted` (NORMAL); `v1.failed = environment_blocked ∪ software_fault` (ABNORMAL). Existing consumers see fewer "failed" than today — because today over-reports failure.

### Lifecycle states (8, no rename)

```
                              ┌─→ needs_help ←─┐
                              │  (transient pause —   │
                              │   bot's selector wait │
                              │   keeps polling)      │
                              ↓                       │
requested → joining → awaiting_admission → active → stopping → completed
   │           │            │                  │         │
   └──────── failed ←──── failed ←──────── failed ←──── failed
```

| state | meaning |
|---|---|
| `requested` | DB row created, bot not yet spawned |
| `joining` | Bot process alive, navigating to meeting URL |
| `awaiting_admission` | In platform's waiting room |
| `active` | In the meeting (DOM reached); audio capture running |
| `needs_help` | Transient pause — VNC exposed for human/AI assist |
| `stopping` | Leaving the meeting; cleanup in progress (transient) |
| `completed` | Terminal — controlled exit (configured/expected path) |
| `failed` | Terminal — error condition prevented normal lifecycle |

**We are NOT renaming our states for recall.ai compatibility.** A `recall_to_vexa` translator is a static lookup table either way (`bot.joining_call → "joining"`); rename gains no translator simplicity but costs migration for every existing webhook consumer with integrations on the current names. Keep what works; map at the translation boundary.

The substantive change is **encoding the audio-confirmation gate without breaking the contract**:

- `meeting.data.audio_confirmed_at` 🟡 — timestamp when first transcript segment lands. Set by the bot (or collector, on receiving the first segment). The classifier reads this to distinguish `active` meetings that produced audio from `active` meetings that didn't.
- `meeting.status` semantics unchanged: `active` still means "bot is in the meeting DOM". Existing consumers don't observe a contract break.
- Outcome classifier uses `audio_confirmed_at` to decide between `success.recorded_*` and `software_fault.audio_pipeline_failed` — the data encodes the truth without renaming the state.

Today's `bot.active ≠ bot.actually_capturing_audio` parallel-surface drift is closed by this flag, with zero migration cost.

For recall.ai mapping, the translator synthesizes the recall name from `(status, audio_confirmed_at)`:

```
recall direction (incoming):
  bot.in_call_not_recording → vexa: status='active', audio_confirmed_at=null
  bot.in_call_recording     → vexa: status='active', audio_confirmed_at=<ts>

vexa direction (outgoing — for users wanting recall-compat webhooks):
  vexa active + null audio_confirmed_at  → emit bot.in_call_not_recording
  vexa active + non-null audio_confirmed_at → emit bot.in_call_recording
```

Other recall name differences (`in_waiting_room` ↔ `awaiting_admission`, `done` ↔ `completed`, `fatal` ↔ `failed`, `call_ended` ↔ `stopping`) are pure renames at the translator layer — no internal change.

### Transition rules

Each row is one specific cause within a transition. Where today's table has multi-class rows, splits the row.

| From | To | Trigger sub-cause | Outcome | Class |
|---|---|---|---|---|
| requested | failed | Schema validation (bad URL/payload) | `user_aborted.invalid_request` | normal |
| requested | failed | Wrong password rejected pre-spawn | `user_aborted.wrong_password_provided` | normal |
| requested | failed | Spawn infrastructure failure | `software_fault.runtime_failed_to_launch` | abnormal |
| requested | done | User DELETE before bot joined | `user_aborted.cancelled_before_join` | normal |
| joining | failed | Wrong password / wrong URL | `user_aborted.wrong_*` | normal |
| joining | failed | Meeting requires signin / 2FA / captcha / org-restricted | `environment_blocked.requires_*` | abnormal |
| joining | failed | Meeting locked / full / not-started / ended | `environment_blocked.*` | abnormal |
| joining | failed | Network drop during navigation | `environment_blocked.network_dropped_connection` | abnormal |
| joining | failed | Selector timeout — UI variant we don't handle (LFX portal) | `software_fault.automation_gap_unresolved` | abnormal |
| joining | failed | Bot JS exception in our code | `software_fault.bot_crashed_pre_join` | abnormal |
| joining | needs_help | Auth wall / portal page detected | (transient — recoverable) | — |
| in_waiting_room | failed | Host rejected / admission timeout | `environment_blocked.host_*` | abnormal |
| needs_help | (resume) | Human/AI clicked, selector wait fired | (back to lifecycle) | — |
| needs_help | failed | User clicked Stop | `user_aborted.cancelled_quickly` | normal |
| needs_help | failed | Escalation timeout (no help came) | `software_fault.automation_gap_unresolved` | abnormal |
| in_call_not_recording | needs_help | `audio_join_failed` (Zoom Web consent) | (transient — recoverable) | — |
| in_call_recording | done | `LEFT_ALONE` / user stopped after segments / max-time | `success.recorded_then_*` | normal |
| in_call_recording | done | `EVICTED` after recording (today: failed — wrong) | `success.recorded_then_host_kicked` | normal |
| in_call_not_recording | fatal | `STOPPED_WITH_NO_AUDIO` (30s+, transcribe-on, 0 segments) | `software_fault.audio_pipeline_failed` | abnormal |
| in_call_not_recording | fatal | `EVICTED` before recording | `environment_blocked.host_kicked_pre_recording` | abnormal |
| in_call_recording | fatal | Bot JS crash mid-meeting | `software_fault.bot_crashed_in_call` | abnormal |
| stopping | (classifier output) | various | resolves to one of the rows above | — |
| call_ended | fatal (sweep) | Stale > 5min, force-finalize | `software_fault.classifier_gap` + classified-by-data | abnormal |

#### Today's mis-routings (the redesign closes)

The transition shape is right; the routing is wrong:

| today's classifier | reality |
|---|---|
| `EVICTED` post-recording → failed | should be `success.recorded_then_host_kicked` (bot delivered value) |
| `MAX_BOT_TIME_EXCEEDED` post-recording → failed | should be `success.recorded_then_max_time` (bot ran the full asked time) |
| schema validation → failed (alarming red) | should be `user_aborted.invalid_request` (normal — user typo) |
| wrong password → failed (alarming red) | should be `user_aborted.wrong_password_provided` (normal) |

### `needs_help` — hybrid automation

🟡 Renamed from `needs_human_help` — the state is broader than human-only assist (an LLM-driven `assist-agent` is the planned 🔵 phase 2 operator using the same Playwright/CDP surface).

The state has three properties that make it part of the success path, not failure:

1. **Browser stays alive.** No `page.close()`, no idle-timeout reaping. VNC server, runtime-api `/touch`, dashboard WS all stay live for the whole pause window.
2. **Resolution is automatic detection of progress.** The bot's existing `page.waitForSelector()` is the resolution mechanism — whoever clicks the right button (human via VNC, or 🔵 AI via Playwright later), the bot sees the next-stage element appear and continues its joining flow. No "I'm done" signal needed.
3. **Resolved meetings end in `success`, not `software_fault`.** The escalation is part of the lifecycle, not the outcome. `software_fault.automation_gap_unresolved` is reserved for the case where help didn't come in time.

#### Canonical example: LFX zoom-portal URL

`https://zoom-lfx.platform.linuxfoundation.org/meeting/<id>?password=<uuid>` renders an extra "Continue" / consent page before redirecting to the actual Zoom Web Client. Bot doesn't know that DOM.

```
T+0    POST /bots; Path 3 schema accepts; bot spawns
T+3s   bot navigates to LFX URL verbatim (white-label passthrough)
T+10s  Pre-emptive escalation — no Zoom name input visible, no waiting-room banner.
       status: joining → needs_help; dashboard shows 🔵 panel with VNC link
T+30s  user opens VNC, clicks "Continue" on portal page
T+37s  LFX redirects to Zoom Web Client; Zoom name input renders
T+39s  bot's selector wait fires (still polling — never paused)
       bot resumes: typeName + clickJoin → in_waiting_room → in_call_recording
T+30m  user ends meeting → call_ended → done
       outcome: success.recorded_then_user_stopped (NORMAL ✅)
```

The user-assist becomes part of the success path. Honest.

#### 🔵 AI as operator (later, same code surface)

The escalation event is the integration point. Replace human VNC viewer with `assist-agent` service that fetches a screenshot, prompts an LLM, executes the action via the same `/b/{meeting_id}/cdp` endpoint. Bot doesn't change; selector wait is the same resolution mechanism.

### Path 3 — meeting URL inputs (white-label support 🟢)

POST /bots accepts URLs in three shapes:
1. `(platform + native_meeting_id)` — canonical
2. URL alone — server parses, extracts `native_meeting_id` for known platforms
3. **`(meeting_url + platform)`** — white-label/enterprise portals

For path 3, the bot navigates the URL **verbatim**. Hostname-anchored detection: only `zoom.us` / `*.zoom.us` URLs with `/j/<digits>` get rewritten to canonical web client; everything else passes through with a 5min selector wait so a human can VNC in. The hostname check is `hostname === 'zoom.us' || hostname.endsWith('.zoom.us')` — substring would false-positive on `zoom-lfx.platform.linuxfoundation.org`.

---

## HOW — implementation

### Lifetime management (4 mechanisms)

Meeting bots use **model 1** (consumer-managed) from runtime-api. `idle_timeout: 0` — runtime-api never auto-stops them. Meeting-api owns the full lifecycle:

| # | mechanism | how |
|---|---|---|
| 1 | **Server-side hard limit** | scheduler job in runtime-api: `execute_at = now + max_bot_time` (default 2h) → `DELETE /bots/internal/timeout/{id}` |
| 2 | **Bot-side timers** | bot self-exits when `no_one_joined_timeout` (2min default) / `max_wait_for_admission` (15min) / `max_time_left_alone` (15min) fires |
| 3 | **User DELETE** | `DELETE /bots/{platform}/{native_id}` → Redis `{action: leave}` → bot exits |
| 4 | **Platform events** | bot detects evicted/ended/disconnected → self-exit |

All 4 paths converge on the classifier (`callbacks.py:_classify_stopped_exit`). The redesign extends to a single central outcome classifier called from every terminal write site.

Resolution order for timeout config: per-request `automatic_leave` → `user.data.bot_config` → system defaults.

### Delayed stop + sweep safety nets 🟢

DELETE flow:
1. Send Redis `{action: leave}`
2. Status → stopping/`call_ended`; **enqueue stop in Redis Stream outbox** (`container_stop_outbox.py`, `fire_at = now + 90s`)
3. If bot exits naturally within 90s → exit callback fires → classifier → terminal
4. Else outbox consumer (in `sweeps.py`) calls `DELETE /containers/{name}` on runtime-api with retries 5x → DLQ

**Why durable outbox** (vs v0.10.4 in-memory `asyncio.create_task`): the in-memory task was lost on meeting-api restart; bot stayed running, meeting stuck in stopping. Outbox is durable across restarts.

**Stale `bot_container_id` detection** 🟢: at DELETE time, validates `container_name` via `GET /containers/{name}`. If 404 or `status != running`, routes through the no-container classifier branch synchronously instead of waiting for the 5-min sweep.

**Stale-stopping sweep** 🟢 (`sweeps.py:_sweep_stale_stopping`): every 60s, scans rows in `stopping` for >5min, force-finalizes via the classifier. Safety net for bots whose exit callback never landed.

### Concurrency

`max_concurrent_bots` counts meetings in non-terminal states (`requested`, `joining`, `in_waiting_room`, `in_call_*`, `needs_help`). `call_ended` is NOT counted — slot released immediately on DELETE so user doesn't wait 90s for the next bot. Two containers may run briefly; only one "active" meeting counts.

### Callbacks (bot → meeting-api)

| Endpoint | Called when |
|---|---|
| `/bots/internal/callback/status_change` | Any state transition (unified) |
| `/bots/internal/callback/exited` | Bot process exits |
| `/bots/internal/callback/joining` / `awaiting_admission` / `started` | Stage-specific transitions |
| 🟢 `/bots/internal/test/session-bootstrap` | Synthetic test rig — gated by `VEXA_ENV != production` |

All callbacks: 3 retries, exponential backoff (1s/2s/4s), 5s timeout each. Status transitions protected by `SELECT FOR UPDATE`.

🟢 **`dry_run: true`** flag (gated by non-prod): creates meeting row + bot_config, skips `_spawn_via_runtime_api`. Used by the synthetic test rig under `tests3/synthetic/` to drive the lifecycle without external dependencies.

### Components

| Component | File | Role |
|---|---|---|
| Bot creation | `services/meeting-api/meeting_api/meetings.py:870-1130` | POST /bots, Path 3 extraction, spawn, dry_run gate |
| Outcome classifier | `services/meeting-api/meeting_api/callbacks.py:52` | `_classify_stopped_exit` — single source of truth for terminal-state routing |
| Stop/timeout | `services/meeting-api/meeting_api/meetings.py:1530-1650` | DELETE, stale-container detection, no-container classifier branch |
| Container-stop outbox | `services/meeting-api/meeting_api/container_stop_outbox.py` | Durable Redis Stream outbox for DELETE container-stop |
| Sweeps | `services/meeting-api/meeting_api/sweeps.py` | Stale-stopping + outbox consumer + aggregation-failure retry |
| Bot core | `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts` | Join, admit, capture flow |
| Zoom Web join | `services/vexa-bot/core/src/platforms/zoom/web/join.ts` | URL parser (canonical rewrite vs white-label passthrough); 5min wait for portals |
| Zoom Web prepare | `services/vexa-bot/core/src/platforms/zoom/web/prepare.ts` | Audio-join 3-attempt loop + `needs_help` escalation |
| Bot exit-reason callbacks | `services/vexa-bot/core/src/utils.ts` | `callJoiningCallback`, `callStartupCallback`, `callNeedsHumanHelpCallback` (🟡 rename to `callNeedsHelpCallback` as part of state rename) |
| Scheduler | `services/runtime-api/runtime_api/scheduler.py` | `max_bot_time` enforcement |
| Runtime exit callback | `services/runtime-api/runtime_api/lifecycle.py` | Pod-event capture (OOMKill, Evicted) + durable callback delivery |
| 🟡 Recall translator | `services/meeting-api/meeting_api/translators/recall.py` (planned) | Static lookup table — recall webhook → vexa state-change |

### Recall.ai mapping summary

7 of 9 vexa states map directly to recall events. Translator is a static lookup table:

```python
RECALL_TO_VEXA_STATE = {
    "bot.joining_call":             "joining",
    "bot.in_waiting_room":          "in_waiting_room",
    "bot.in_call_not_recording":    "in_call_not_recording",
    "bot.in_call_recording":        "in_call_recording",
    "bot.recording_permission_denied": "needs_help",  # special — vexa exposes VNC
    "bot.recording_permission_allowed": None,               # drop — Meeting SDK only
    "bot.call_ended":               "call_ended",
    "bot.done":                     "done",
    "bot.fatal":                    "fatal",
    "bot.breakout_room_*":          None,                   # drop
}

# (state, sub_code) → (outcome, cause): translator does the agency lift
# Recall's done/fatal cut is "controlled exit vs error" — coarser than our
# 4-bucket. Their sub_code names already encode agency; translator extracts.
RECALL_TO_VEXA_OUTCOME = {
    ("bot.fatal", "meeting_password_incorrect"):   ("user_aborted", "wrong_password_provided"),
    ("bot.fatal", "meeting_locked"):               ("environment_blocked", "host_locked_meeting"),
    ("bot.fatal", "bot_errored"):                  ("software_fault", "unknown_error"),
    ("bot.done",  "timeout_exceeded_everyone_left"):("success", "recorded_then_left_alone"),
    ("bot.done",  "bot_kicked_from_call"):         # split on reached_recording:
                                                    # success.recorded_then_host_kicked or
                                                    # environment_blocked.host_kicked_pre_recording
    # ... ~80 entries
}
```

**Key insight on the cuts:** recall's `bot.fatal` is NOT vexa's `abnormal`. Recall puts user input errors (`meeting_password_incorrect`) in fatal because their billing model says "don't charge for those" — customer-friendly but it makes their fatal rate useless as an engineering signal. Our 4-bucket fixes this — `software_fault` is engineering's bucket alone.

---

## DoD


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.5.3-260502-1637`. Do not edit by hand — edit the sidecar `dods.yaml` + re-run `make -C tests3 report --write-features`. -->

**Confidence: 92%** (gate: 90%, status: ✅ pass)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| create-ok | POST /bots spawns a bot container and returns a bot id | 15 | ✅ pass | `helm`: containers/create: bot 1 created |
| create-alive | Bot process is running 10s after creation (not crash-looping) | 15 | ✅ pass | `helm`: containers/alive: bot process running after 10s |
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ✅ pass | `lite`: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no route collision with /bots/{meeting_id}; `compose`: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no route collision with /bots/{meeting_id}; `helm`: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/st… |
| removal | Container fully removed after DELETE /bots/... | 10 | ✅ pass | `helm`: containers/removal: container fully removed after stop |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ✅ pass | `helm`: containers/status_completed: meeting.status=completed after stop (waited 1x5s) |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ✅ pass | `lite`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; `compose`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; `helm`: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping trea… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ✅ pass | `lite`: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; `compose`: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; `helm`: smoke-static/ROUTE_COLLISION: bot detail r… |
| timeout-stop | Bot auto-stops after automatic_leave timeout (no_one_joined_timeout) | 10 | ⚠️ skip | `helm`: containers/timeout_stop: bot still running after 60s (timeout may count from lobby) |
| concurrency-slot | Concurrent-bot slot released immediately on stop — next create succeeds | 10 | ✅ pass | `helm`: containers/concurrency_slot: slot released, B created (HTTP 201) |
| no-orphans | No zombie/exited bot containers left after a lifecycle run | 10 | ✅ pass | `helm`: containers/no_orphans: no exited/zombie containers |
| status-webhooks-fire | Status-change webhooks fire for every transition when enabled in webhook_events | 5 | ✅ pass | `helm`: webhooks/e2e_status: 5 status-change webhook(s) fired: meeting.status_change |
| recording-incremental-chunk-upload | bot uploads each MediaRecorder chunk as it arrives; meeting-api accepts chunk_seq on /internal/recordings/upload | 15 | ✅ pass | `lite`: smoke-static/RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ: /internal/recordings/upload accepts chunk_seq: int form parameter; `compose`: smoke-static/RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ: /internal/recordings/upload accepts chunk_seq: int form parameter |
| bot-records-incrementally | bot recording.ts calls MediaRecorder.start with ≥15s timeslice AND uploads each chunk via __vexaSaveRecordingChunk | 10 | ✅ pass | `lite`: bot-records-incrementally/BOT_RECORDS_INCREMENTALLY: ≥15s MediaRecorder timeslice + __vexaSaveRecordingChunk wired in shared modules (browser.ts + audio-pipeline.ts); no per-platform regression; `compose`: bot-records-incrementally/BOT_RECORDS_INCREMENTALLY: ≥15s MediaRecorder timeslice +… |
| recording-survives-mid-meeting-kill | SIGKILL mid-recording leaves already-uploaded chunks durable in MinIO; Recording.status stays IN_PROGRESS until is_final=true | 10 | ✅ pass | `compose`: recording-survives-sigkill/RECORDING_SURVIVES_MID_MEETING_KILL: chunk_seq contract verified statically (see RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ) |
| runtime-api-stop-grace-matches-pod-spec | runtime-api delete_namespaced_pod grace_period_seconds matches the pod spec terminationGracePeriodSeconds | 5 | ✅ pass | `helm`: smoke-static/RUNTIME_API_STOP_GRACE_MATCHES_POD_SPEC: runtime-api kubernetes.py stop() passes its `timeout` parameter through as grace_period_seconds on pod deletion — bot graceful-leave has the full grace window. Current default: 60s (matches pod spec's terminationGracePeriodSeconds=60). |
| runtime-api-exit-callback-durable | runtime-api exit callback delivery is durable across consumer outages (idle_loop re-sweeps pending records) | 10 | ✅ pass | `compose`: runtime-api-exit-callback-durable/RUNTIME_API_EXIT_CALLBACK_DURABLE: durable-delivery contract covered by idle_loop_sweeps + no_delete_on_exhaustion static checks above |
| runtime-api-idle-loop-sweeps-pending-callbacks | services/runtime-api lifecycle.py idle_loop iterates pending callbacks each tick and retries delivery | 5 | ✅ pass | `lite`: smoke-static/RUNTIME_API_IDLE_LOOP_SWEEPS_PENDING_CALLBACKS: runtime-api idle_loop references list_pending_callbacks — the durable-delivery sweep is wired; `compose`: smoke-static/RUNTIME_API_IDLE_LOOP_SWEEPS_PENDING_CALLBACKS: runtime-api idle_loop references list_pending_callbacks — the… |
| bot-video-default-off | POST /bots `video` field defaults to False — video recording is opt-in, not opt-out | 5 | ✅ pass | `lite`: smoke-static/BOT_VIDEO_DEFAULT_OFF: POST /bots `video` field defaults to False — video recording is opt-in; audio-only is the default for transcription-focused deployments; `compose`: smoke-static/BOT_VIDEO_DEFAULT_OFF: POST /bots `video` field defaults to False — video recording is opt-i… |
| hallucination-corpus-present | bot hallucination corpus (en, es, pt, ru) exists at services/vexa-bot/core/src/services/hallucinations/ — non-empty, ≥5 phrases each | 5 | ✅ pass | `lite`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_PRESENT: 4 langs × non-empty corpus = 167 phrases; `compose`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_PRESENT: 4 langs × non-empty corpus = 167 phrases; `helm`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_PRESENT: 4 l… |
| hallucination-corpus-gitignore-exception | .gitignore has the negation rule '!services/vexa-bot/core/src/services/hallucinations/*.txt' protecting the corpus from the global '*.txt' ignore | 5 | ✅ pass | `lite`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_GITIGNORE_EXCEPTION: .gitignore exception protects corpus from silent re-disappearance; `compose`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_GITIGNORE_EXCEPTION: .gitignore exception protects corpus from silent re-disappearance… |
| hallucination-corpus-build-fail-loud | core/package.json build script uses '&&' (fail-fast) for the cp step, not '2>/dev/null;' (silent-fail) — corpus copy failure aborts build | 5 | ✅ pass | `lite`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_BUILD_FAIL_LOUD: build script uses '&&' chain — cp failure aborts build; `compose`: v0.10.5.3-hallucination-corpus/HALLUCINATION_CORPUS_BUILD_FAIL_LOUD: build script uses '&&' chain — cp failure aborts build; `helm`: v0.10.5.3-hallucinat… |
| shared-audio-pipeline-module-exists | services/vexa-bot/core/src/services/audio-pipeline.ts exports UnifiedRecordingPipeline + MediaRecorderCapture + PulseAudioCapture — single bot-side capture module driving all 3 platforms | 5 | ✅ pass | `lite`: v0.10.6-static-greps/SHARED_AUDIO_PIPELINE_MODULE_EXISTS: audio-pipeline.ts exports UnifiedRecordingPipeline + capture sources; `compose`: v0.10.6-static-greps/SHARED_AUDIO_PIPELINE_MODULE_EXISTS: audio-pipeline.ts exports UnifiedRecordingPipeline + capture sources; `helm`: v0.10.6-static… |
| gmeet-recording-uses-shared-pipeline | googlemeet/recording.ts imports UnifiedRecordingPipeline + MediaRecorderCapture from services/audio-pipeline (Pack U.2 — no longer hand-rolls MediaRecorder boilerplate) | 10 | ✅ pass | `lite`: v0.10.6-static-greps/GMEET_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `compose`: v0.10.6-static-greps/GMEET_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `helm`: v0.10.6-static-greps/GMEET_RECORDING_USES_SHARED_PIPELINE: imports from serv… |
| teams-recording-uses-shared-pipeline | msteams/recording.ts imports UnifiedRecordingPipeline + MediaRecorderCapture from services/audio-pipeline (Pack U.3) | 10 | ✅ pass | `lite`: v0.10.6-static-greps/TEAMS_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `compose`: v0.10.6-static-greps/TEAMS_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `helm`: v0.10.6-static-greps/TEAMS_RECORDING_USES_SHARED_PIPELINE: imports from serv… |
| zoom-web-recording-uses-shared-pipeline | zoom/web/recording.ts imports UnifiedRecordingPipeline + PulseAudioCapture; chunked-upload model (Pack U.4 — pre-Pack-U: total audio loss on bot crash) | 10 | ✅ pass | `lite`: v0.10.6-static-greps/ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `compose`: v0.10.6-static-greps/ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE: imports from services/audio-pipeline; `helm`: v0.10.6-static-greps/ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE: imports … |
| zoom-web-uploads-chunks-periodically | PulseAudioCapture in audio-pipeline.ts emits 15s WAV chunks during a Zoom meeting (uploadChunk fires multiple times before finalize) | 10 | ✅ pass | `lite`: v0.10.6-static-greps/ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY: PulseAudioCapture class present in audio-pipeline.ts; `compose`: v0.10.6-static-greps/ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY: PulseAudioCapture class present in audio-pipeline.ts; `helm`: v0.10.6-static-greps/ZOOM_WEB_UPLOADS_CHUNKS… |
| platform-recording-ts-line-budget | after Pack U unification, every platform recording.ts is within LOC budget (gmeet ≤ 800, msteams ≤ 1000, zoom/web ≤ 200) — captures the duplication-removal as a static guard | 5 | ✅ pass | `lite`: v0.10.6-static-greps/PLATFORM_RECORDING_TS_LINE_BUDGET: all platform recording.ts within budget |
| no-per-platform-master-construction | no platform recording.ts retains __vexaSaveRecordingBlob or __vexaRecordedChunks master-blob assembly — master is exclusively server-side | 10 | ✅ pass | `lite`: v0.10.6-static-greps/NO_PER_PLATFORM_MASTER_CONSTRUCTION: no bot-side master construction in platform recording.ts |
| bot-kill-recording-playable-gmeet | after SIGKILL'ing a GMeet bot mid-recording, server-side finalize_recording_master builds master.webm from chunks already in MinIO → ffprobe-playable. Crash-safety the bot couldn't provide pre-Pack-U. (weight 3: requires fixture meeting URL — operator-driven via scope.yaml human_verify; 0% gate-pull when fixtures absent) | 3 | ⬜ missing | `compose`: v0.10.6-runtime-smokes/BOT_KILL_RECORDING_PLAYABLE_GMEET: FIXTURE_GMEET_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify; `helm`: check BOT_KILL_RECORDING_PLAYABLE_GMEET not found in any report |
| bot-kill-recording-playable-teams | Teams equivalent — SIGKILL bot, master built post-callback, ffprobe-playable. (weight 3: fixture-dependent) | 3 | ⬜ missing | `compose`: v0.10.6-runtime-smokes/BOT_KILL_RECORDING_PLAYABLE_TEAMS: FIXTURE_TEAMS_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify; `helm`: check BOT_KILL_RECORDING_PLAYABLE_TEAMS not found in any report |
| bot-kill-recording-playable-zoom | Zoom Web equivalent — SIGKILL bot, master.wav built from chunked PulseAudio uploads, ffprobe-playable. Pre-Pack-U Zoom crash = total audio loss; this DoD certifies the recovery. (weight 3: fixture-dependent) | 3 | ⚠️ skip | `compose`: v0.10.6-runtime-smokes/BOT_KILL_RECORDING_PLAYABLE_ZOOM: FIXTURE_ZOOM_URL not set — operator-driven; see scope.yaml human_verify |

<!-- END AUTO-DOD -->


## Failure modes

**Tracked registry:** [`./failure-modes.md`](./failure-modes.md) — every prod failure observed by PLATFORM (or anyone) lands here as a tracked `FM-NNN` entry with status, linked fixes, repro, and pre-classification against the v0.10.6 outcome taxonomy. Standing rule from team-lead 2026-04-28.

The table below is the historical lessons catalog — kept for the "Learned" column. New entries go in the registry.

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| **47% of completed meetings had 0 transcripts** (#255 prod data) | callback handler defaulted any clean exit to `status=completed` regardless of `completion_reason` | 🟢 routing-rule classifier — routes by `completion_reason` + `reached_active` + `segment_count` | Silent success kills observability — but defaulting-to-failed is wrong too; outcome taxonomy is the principled fix |
| Bot at status=active forever, 0 transcripts (Zoom Web) | Audio-join 3-attempt loop fell through silently. No `<audio>` elements created → per-speaker capture bails | 🟢 commit `37316d6` — escalate `audio_join_failed` to `needs_help` | `bot.active` ≠ `bot.actually_capturing_audio` — closed by `in_call_not_recording` / `in_call_recording` split |
| LFX/AWS/enterprise Zoom URL rejected by parser | Bot only matched canonical `/j/<id>` path | 🟢 commit `da97506` — canonical `*.zoom.us` rewrites; white-label passthrough + 5min wait | Per-vendor parsers are an arms race we lose; trust user-supplied platform pick (Path 3) |
| Meeting stuck in `stopping` after stack restart | DELETE read truthy `bot_container_id` and skipped no-container branch even though container was wiped | 🟢 commit `e16fa7e` — validate `bot_container_id` via runtime-api inspect | Sweep is the safety net, not the first line of defense |
| `failure_stage: active` paints over reality | `failure_stage` defaults to `ACTIVE` when callback omits it (`callbacks.py:312`) | 🟡 redesign — replace default with `unknown` | Stacked defaults compound (bot's `self_initiated_leave` default + server's `ACTIVE` default = lying user message) |
| Bot shows "failed" after successful meeting | `exit_code=1` on `self_initiated_leave` treated as failure | callbacks.py: exit during stopping → completed | Graceful leave ≠ crash |
| Bot stuck on name input (unauthenticated GMeet) | No saved cookies, Google shows "Your name" prompt | Bot should fill name or fail fast | Open bug |
| Recording upload fails on lite (host network) | MinIO endpoint `http://minio:9000` unresolvable | Set `MINIO_ENDPOINT=http://localhost:9000` for lite | `recordings.py:194`, `storage.py:90` |
| MinIO retry blocks bot completion (lite) | Lite has no MinIO; `recording_enabled` defaults true; every stop hits 30s DNS-fail retry | Set `RECORDING_ENABLED=false` in lite .env when no MinIO | `meetings.py:796`, `storage.py:90` |
| K8s exit callback not fired from DELETE | `DELETE /containers/{name}` calls `state.set_stopped()` but NOT `_fire_exit_callback()`; only fires from pod watcher | Fix: fire exit callback from DELETE endpoint | `runtime-api/api.py:308-318` |
| Redis client stays None after startup failure | Meeting-api never reconnects if Redis was down at boot | Restart meeting-api after Redis recovers | `main.py:101-113` |
| Auto-admit clicks text node | `text=/Admit/i` matched non-clickable element | Multi-phase CDP: panel → expand → `button[aria-label^="Admit "]` | Always use element-type + aria-label |
| "Waiting to join" section collapsed | Google Meet collapses lobby list after ~10s | Expand before looking for admit button | Check visibility before assuming DOM state |

---

## Roadmap

🟢 **v0.10.5 — IN FLIGHT on `release/260427`, NOT YET SHIPPED.**
Patches landed on the release branch during develop iterations: routing-rule classifier (replaces "default to completed" with completion_reason-driven routing), Path 3 trust model for white-label URLs, durable container-stop outbox, stale-stopping sweep, stale `bot_container_id` detection, `audio_join_failed` escalation, white-label URL passthrough + 5min wait, synthetic test rig, `dry_run` flag. **All shipped to the dev image, none promoted to `:latest`.** `:latest` still points to the previous release. Symptom-level fixes; inherits binary `completed | failed` framing.

🟡 **Lifecycle redesign — being decided now.**
Decision in progress: does v0.10.5 ship with the symptomatic patches as-is, OR do we extend v0.10.5 scope to include the outcome-taxonomy redesign before shipping? The redesign content:

- 4-bucket × 2-class outcome taxonomy (`success` / `user_aborted` / `environment_blocked` / `software_fault` × normal/abnormal) — new fields `data.outcome` / `data.outcome_class` / `data.cause`
- One central outcome classifier called from every terminal write site (replaces today's scattered classifier calls — every miss in v0.10.5 was a parallel-write-site bug)
- `meeting.data.audio_confirmed_at` flag — encodes the audio-confirmation gate without renaming `active` (closes `bot.active ≠ bot.actually_capturing_audio` parallel-surface drift; zero contract break for existing webhook consumers)
- Rename `needs_human_help` → `needs_help` (state is broader than human-only — AI assist-agent later)
- Reframe `needs_help` as first-class hybrid-automation pause (not failure path)
- Proactive escalation on unknown UI; configurable `needs_help_timeout`
- Dashboard 5-class visual treatment (✅⚪⚠🔵🔴) reading from `data.outcome`
- Webhook v2 with new fields; v1 keeps `completed | failed` derived via outcome → status mapping
- `recall_to_vexa` translator (static lookup table — no internal renames; mapping at translation boundary)

**Explicitly NOT in scope** (despite earlier draft): bulk lifecycle state renames for recall.ai-alignment (`awaiting_admission` → `in_waiting_room`, `active` → split, `stopping` → `call_ended`, `completed` → `done`, `failed` → `fatal`). Migration cost for us + every webhook consumer doesn't pay back; the translator handles name mapping either way.

Estimated ~3-4 days dev across meeting-api / dashboard / docs.

The current auto-DoD confidence (91%) measures behavioral checks against today's lifecycle. The redesign brings **design-confidence** up but won't move the auto-DoD number until new behavioral checks for outcome classification + audio-confirmation gate land in `dods.yaml`.

🔵 **Later.** `assist-agent` service (LLM-driven Playwright control via the same `/b/{token}/cdp` endpoint VNC viewer uses); pause-and-resume bot model (required for AI exclusive-control window); per-portal automation accumulation (LFX, AWS Chime, Bloomberg — reduces `software_fault.automation_gap_unresolved` rate over time as we cover more patterns).
