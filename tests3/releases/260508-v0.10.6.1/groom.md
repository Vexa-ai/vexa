# Groom — post-v0.10.6 (filed 2026-05-03, refreshed 2026-05-08)

## Human approval (2026-05-08)

Maintainer signal in groom turn: "let's go for it" — approves the
low-hanging-fruit set below for the next release cycle, **with #289
removed** (maintainer says it does not match current prod behavior;
re-triage before reconsidering).

```yaml
approved: true
scope: low-hanging-fruit-260508
release_id: 260508-v0.10.6.1
included:
  tier_1_hotfix:
    - "#314 multi-chunk playback first-30s"
    - "#315 + #308 /speak prod outage / TTS pod CrashLoop"
    - "#313 browser_session DELETE stuck-in-stopping"
    - "#311 post_meeting / recording_finalizer master race"
    - "PR-merge sweep: #319, #239, #283"
  tier_2_durable:
    - "vexa-lite docs/env hygiene (OPENAI_API_KEY, MIN_AUDIO_S, Mac note)"
    - "WebM Duration tag (Pack U.8 / #302 follow-up)"
    - "GMeet rejection + waiting-room fast-fail (#316)"
    - "arm64 / Apple Silicon Docker images (#321)"
  tier_3_hygiene:
    - "broad-except narrow (#306)"
    - "chunk_write prior_count log fix (#312)"
    - "stale-issue audit sweep (#166, #113, #128, #96, #198)"
explicitly_removed:
  - "#289 — re-triage; framing does not match prod"
deferred_to_later_cycle:
  - "Audit-stage wiring (#303)"
  - "Bot broadcast-surface regression (interactive-features class)"
  - "K8s container_id orphan-bot (#261)"
  - "Long-recording transcribe pipeline (#243 + #241)"
  - "Bot-lifecycle classifier hardening (#292 + #294 + #301)"
  - "Zoom reliability (#325, #326)"
  - "Discord fetcher in-repo"
  - "Everything in clusters A–M not listed above"
```

Stage exit condition for `groom` is satisfied: `groom.md` exists and human
has marked at least one pack `approved: true`. Next stage: **plan**.

---


> Refresh 2026-05-08: folded URGENT v0.10.6 regressions (#311, #313, #314, #315),
> new bug reports (#316–#318, #322, #323), feature requests (#321, #324),
> open community PR triage (#320, #319, #297, #283, #260, #239), and a 21-day
> Discord pull (last 21d, channels: #bug-reports, #feature-requests,
> #general-chat, #ms-teams-integration, #audio-video-recording,
> #speaker-identification, #discord-voice-integration, #hey-vexa-speaking-bot).
>
> Discord pull was a one-shot fetch via the existing out-of-repo bot token
> (`/home/dima/dev/0_old/skills/discord/.env`) directly to Discord REST API —
> nothing committed. The in-repo fetcher mandated by stage 01-groom §3 is
> **still missing**; this groom round should produce a follow-up issue to
> finally land it (intake leak called out in `tests3/README.md`).


## What just shipped
v0.10.6 — full inventory + ship trail in `tests3/releases/260501-chunk-leak/`.
GitHub release: https://github.com/Vexa-ai/vexa/releases/tag/v0.10.6

5 issues closed against v0.10.6:
- #281 — Teams 44ms admission drop (SDP-munge complete revert)
- #288 — /raw whole-file memory buffer (Pack D-3 streams direct from MinIO)
- #290 — media_files N-entries race (Pack U.7 chunk_write defensive guard)
- #296 — recording-paths divergence (Pack U.4 unification)
- #304 — dashboard pagination duplicates (Pack U.7 _offset cursor)

## URGENT — v0.10.6 regressions (hotfix candidates for v0.10.6.1)

### Pack — Multi-chunk playback truncated to first 30s (#314)
**Why it's here**: prod regression on ~73 multi-chunk meetings. `master.webm` is
appended at the *end* of `media_files`, but dashboard `.find()` returns chunk 0.
Visible breakage for paying users who have multi-segment recordings.
**Repro confidence**: high (concrete prod meeting list cited in #314)
**Estimated scope**: 2–4 hours. Two-pronged:
- Backfill: re-order `media_files` so `master.webm` is the canonical entry
  (or change reader to prefer `kind=master`).
- Forward: write `master.webm` as element 0 / promote-on-finalize.

### Pack — TTS prod outage + /speak silently dropped (#315, #308)
**Why it's here**: v0.10.6 shipped with `voice_agent_enabled` users effectively
muted: TTS pod in CrashLoopBackOff (154 restarts) and bot ignores `provider`
param so falls through. Two intertwined defects:
- TTS pod boot failure (likely the helm probe-delay fix in 9fde7d2 was
  incomplete, or model download path failing in prod cluster).
- bot `/speak` handler doesn't honor `provider` selection — falls back without
  signaling.
**Repro confidence**: high (any prod /speak)
**Estimated scope**: 1 day. Diagnose pod logs first; the bot-side provider bug is ~1h once root cause known.

### Pack — browser_session DELETE leaves meeting stuck in 'stopping' (#313)
**Why it's here**: runtime-api DELETE skips exit callback when no
`connection_id`; Pack E.3.2 stuck-meeting sweep is defeated because
webhook-retry bumps `updated_at`. Net effect: meetings leak in 'stopping' state
indefinitely.
**Repro confidence**: high (deterministic on DELETE without connection_id)
**Estimated scope**: 4–8 hours. Two fixes: synthesize exit callback in
runtime-api DELETE path AND change sweep predicate to use a stable
"last_progress_at" rather than `updated_at`.

### Pack — Pack U.7 second race: post_meeting stomps recording_finalizer master (#311)
**Why it's here**: `post_meeting.finalize_in_progress_recordings` and
`recording_finalizer` can both write `master_path` for the same recording.
Race-window narrow but real, and undermines U.7's correctness claim.
**Repro confidence**: medium (timing-dependent; reproducer requires forced
overlap)
**Estimated scope**: 4 hours — single advisory lock keyed on recording_id, or
let only `recording_finalizer` write the master and have post_meeting observe.

### Pack — chunk_write prior_count plateau (#312)
**Why it's here**: cosmetic log defect; included for completeness because it
was filed against v0.10.6 and may surface in audit log review.
**Repro confidence**: high
**Estimated scope**: 30 min.

## New product bugs (non-regression)

### Pack — Zoom reliability: ASF meetings (#326) + scheduled host-busy (#325)
**Why it's here**: maintainer-reported signal during this groom round, now
filed. Two distinct Zoom failure modes the bot does not currently survive:

1. **#326** — Apache Software Foundation (ASF) hosted Zoom meetings fail
   entirely. ASF runs its own Zoom org with bespoke admission/registration
   policies (registration-required attendee flow, attendee-info forms,
   community-confidential meeting metadata gates). Distinct DOM/admission
   flow from the consumer Zoom Web path covered by #318.
2. **#325** — Scheduled Zoom meetings fail when host is "in another
   meeting". Back-to-back scheduled meetings with an overrunning prior host
   show "Your host is currently in another meeting" instead of the normal
   join flow. Bot does not detect, does not retry, times out. Direct
   sibling of #316 (GMeet host-not-started) but for Zoom.

**Repro confidence**: high for #325 (every overrunning prior meeting);
medium for #326 — needs an ASF meeting link to reproduce.

**Estimated scope**: 1–2 days investigation + 4–8h fix per symptom. Reuse
the detection-and-retry policy pattern proposed for #316: detect transient
host state, fast-fail with classified reason, single short retry.

**Relationship**: #326 may be naturally unblocked by #253 (Zoom Meeting SDK
recovery) rather than fixing the Web path — decide before investing.

### Pack — Zoom Web per-speaker pipeline broken on modern Zoom client (#318)
**Why it's here**: bot scrapes `<audio>.srcObject` DOM elements that no longer
exist on current Zoom client; 0 transcripts on real meetings. Confirmed live
mid-call. Independent of #305 (audio quality) — this is total transcript loss
for Zoom Web. Likely competes with the Zoom Meeting SDK epic (#253) for
priority.
**Repro confidence**: high (any Zoom Web meeting)
**Estimated scope**: 1–3 days research; probable rewrite of capture path.

### Pack — Speaker ID degrades with 5+ participants (#317)
**Why it's here**: customer-visible quality regression / known limit; relates
to renderer-state drift (#300) and segment-reconciliation epic (#256).
**Repro confidence**: high (any 5+ participant meeting)
**Estimated scope**: research-heavy. Defer unless paired with #256 epic.

### Pack — GMeet rejection page silently times out (#316)
**Why it's here**: bot waits 120s on hidden name input; no retry policy for
transient "host not started" case. Directly degrades GMeet admission UX and
costs us bot-pod-minutes.
**Repro confidence**: high (any GMeet meeting where host hasn't started)
**Estimated scope**: 4–8 hours. Detect the rejection page, fast-fail with
classified reason, and add a single short retry for host-not-started.

### Pack — Hosted admin & download regressions (#322, #323)
**Why it's here**: two hosted-only reports filed same day. (#322) admin can't
access user console with admin creds; (#323) audio download impossible. Both
need triage to separate legit bugs from auth/RBAC misconfig.
**Repro confidence**: low (need-more-info)
**Estimated scope**: triage 1h; fix unknown. **Action**: label `needs-more-info`
and ask reporter for steps before pulling into a cycle.

## Feature requests (community)

### Pack — Multi-arch Docker images for arm64 / Apple Silicon (#321)
**Why it's here**: recurring community ask; unblocks contributor onboarding on
Mac. Pure CI/build work.
**Repro confidence**: n/a (build-time)
**Estimated scope**: 4–8 hours (buildx matrix + smoke).

### Pack — 2FA via authenticator app (#324)
**Why it's here**: hosted-tier ask. Out of scope for an INNER hotfix cycle;
park behind a security epic.
**Estimated scope**: 2–3 days.

## Discord signal (last 21d)

### Strong corroborations of existing packs
- **#315 /speak prod outage** — `customer-A` (paying enterprise consulting
  customer) reported /speak undelivered to Teams legacy URL on 2026-05-04;
  Dmitry confirmed "/speak isn't being delivered in prod right now, not just
  on legacy URLs. Hotfix coming." Customer is blocked on their core
  consultant-coaching use case — this is the **highest-pressure** v0.10.6.1
  driver. Customer already asked publicly when the hotfix lands (2026-05-05).
- **#321 arm64 images** — `customer-F` filed both Discord and GitHub on M4
  Apple Silicon AMD64-only manifests. Real friction (10-service local stack
  via Rosetta).
- **#316 GMeet admission** — `customer-D`: GMeet bot in waiting room is
  booted by Google after ~5 min regardless of `max_wait_for_admission`; no
  retry / rejoin logic. Adds a new sub-symptom to pack #316 (rejoin policy,
  not just fast-fail).
- **interactive-features regression** — `customer-C` (k8s heavy tester)
  reports on 2026-05-06 (compose, latest): /speak audio-URL **not delivered**,
  image share, web page share, video share, HTML render, and avatar **all
  return 202 but never appear in meeting**. Dmitry confirmed: "we were 100%
  into stabilizing core features since the major refactor in 0.10, so some of
  the secondary features regressed." This is bigger than #315 alone — it's a
  **class regression of the whole bot-broadcast surface**.

### New items NOT yet on GitHub
| theme | reporter / date | summary |
|-------|-----------------|---------|
| **Legacy Teams enterprise URL `/l/meetup-join/...`** | customer-A 2026-05-04 | URLs without numeric meeting ID get assigned an internal hex bot ID (e.g. `9ebed5987bf0fd52`). Docs already mention "raw URL endpoint planned (#161)". Confirmed bot joins, transcribes, chats fine on legacy URL — only /speak fails on this format. Worth promoting #161 from epic-stub to a real pack. |
| **vexa-lite ignores `MIN_AUDIO_S` env var** | customer-E 2026-05-06 | Documented env var doesn't actually shorten min audio chunk. Either docs wrong or wiring missing. Small but customer-confusing. |
| **k8s GET /transcripts returns bot logs instead of transcripts** | customer-C 2026-05-06 | Helm deployment, fresh install. Likely router/auth misconfig in helm path; needs repro. |
| **Deepgram OpenAI-compat adapter mismatch** | customer-C 2026-05-04 → 2026-05-05 | `https://api.deepgram.com/v1/listen` returns 404 against vexa transcriber-adapter; expects either WebSocket (`wss://`) or different path. Adapter currently advertised as "OpenAI-compatible should work" but Deepgram is *not* OpenAI-compatible at that path. |
| **Groq `/openai/v1/audio/transcriptions` not working** | customer-C 2026-05-05 | Groq's OpenAI-compatible transcription endpoint failing despite expectation. Needs adapter trace. |
| **Helm chart missing transcription-service template** | customer-G 2026-04-22 | Self-host flag referenced in docs but no helm template wires it. Concrete contributor-onboarding blocker. |
| **vexa-lite on Apple Silicon: bot exits `self_initiated_leave`** | customer-B 2026-04-27 | AMD64-on-M1 emulation breaks browser automation. Tied to #321 but distinct from arm64-images: *also* needs docs note ("not tested on Mac"). |
| **/speak hosted: bot browser has no microphone permissions** | customer-B 2026-04-27 | dashboard.vexa.ai /speak success returns 202 but bot mic permission denied. Hosted-only sub-symptom of #315/general bot-broadcast regression. |
| **vexa-lite docs reference removed `OPENAI_API_KEY` env** | customer-B 2026-04-27 | `docs.vexa.ai/vexa-lite-deployment` still includes `OPENAI_API_KEY` which is no longer in use. Trivial docs fix; surfaces a docs-vs-code drift. |
| **GMeet waiting-room booted after 5min** | customer-D 2026-05-06 | Google evicts the bot from the waiting room after ~5 min; bot does not retry. Folds into #316 pack but adds an explicit rejoin requirement. |

### Discord-only feature signals (low-priority, parking)
- Discord voice channel as a meeting target (customer-E).
- Better TTS model — customer-A explicitly: "the speak feature is the key
  interaction layer; current model is basic." Already noted; reinforces
  v0.10.7+ work to upgrade TTS model after the hotfix lands /speak delivery.
- Configurable model selection for transcription (customer-H) — already filed as
  #232; corroborates priority for self-hosters using LiteLLM proxy.

### New packs from Discord

#### Pack — Bot broadcast-surface regression (interactive features class)
**Why it's here**: post-0.10 refactor regressed *all* secondary
broadcast/interactive features, not just /speak: image share, web page share,
video share, HTML render, avatar, audio URL playback. Each returns 202 but
never reaches the meeting. Confirmed in two independent Discord reports
(`customer-C` self-host, `customer-B` hosted) and acknowledged by maintainer.
**Repro confidence**: high (multiple users, multiple environments)
**Estimated scope**: 2–4 days. Likely a single missing wire in the
post-refactor bot command pipeline (the dispatch returns 202 before
verifying the renderer received the payload). Suggested first step:
end-to-end smoke matrix in `tests3/tests/` covering all `/bots/*/speak`,
`/screen`, `/avatar`, `/render` endpoints with assertion that the
artifact appears in the meeting (or fails with 4xx, never silent 202).
**Relationship**: subsumes #315 once /speak delivery is fixed; #315 stays a
hotfix subset.

#### Pack — Legacy Teams `/l/meetup-join/` URL support (#161 promote)
**Why it's here**: enterprise Teams customers (customer-A, paying) hit URLs
that don't expose a numeric meeting ID. Docs already promise this in #161.
Today: bot joins/transcribes/chats fine, but /speak fails because the
internal hex bot ID isn't a routable meeting key for /speak.
**Repro confidence**: high
**Estimated scope**: 1–2 days. After #315 hotfix, validate /speak on legacy
URL specifically. Likely route normalization + /speak dispatch path keying.

#### Pack — Helm chart parity + transcription-service template
**Why it's here**: `customer-G` (self-hoster) — helm chart silently lacks
a transcription-service deployment template; doc references a flag that has
no template. Combined with `customer-C` k8s issues (TTS not deploying — see
9fde7d2 partial fix; transcripts endpoint returning logs), helm chart is the
weakest deployment surface right now.
**Repro confidence**: high
**Estimated scope**: 1–2 days. Add transcription-service helm template +
smoke install path.

#### Pack — Adapter docs reality check (Deepgram, Groq, OpenAI-compat truth table)
**Why it's here**: maintainer messaging says "any OpenAI-compatible endpoint
should work" but Discord shows Deepgram (`/v1/listen`, expects WS) and Groq
(`/openai/v1/audio/transcriptions`) both fail through current adapter.
Customers waste hours discovering this.
**Repro confidence**: high
**Estimated scope**: 4 hours docs + 4 hours small adapter fixes (Groq
should likely "just work"; needs trace). Outcome: a tested compatibility
matrix in docs naming exact endpoints + headers per provider.

#### Pack — vexa-lite docs + env hygiene
**Why it's here**: small but compounding intake friction:
- `OPENAI_API_KEY` referenced in docs but unused in code.
- `MIN_AUDIO_S` documented but not honored in vexa-lite.
- "not tested on Mac" not surfaced anywhere; users hit Rosetta breakage.
**Repro confidence**: high
**Estimated scope**: 2–4 hours.

#### Pack — In-repo Discord fetcher (close the intake leak)
**Why it's here**: this groom round's Discord pull required reaching outside
the repo to a one-shot script. `tests3/README.md` already calls this an
intake leak ("Market signal breaks silently"). 01-groom step 3 mandates an
in-repo fetcher.
**Repro confidence**: n/a (tooling)
**Estimated scope**: 4–8 hours. Ship `tests3/lib/discord_fetch.py` with a
read-only Bot scope token sourced from a dev-secret file (mirroring the
existing `.env`), one-shot CLI mode + a "since" window, no message storage.
**Note**: small — could land alongside #303 audit-stage wiring as part of
"groom-stage tooling" pack.

## Open PR triage

Carry-over: every cycle is leaking community PRs. Need explicit decisions.

| PR | Author | Status recommendation |
|----|--------|-----------------------|
| #320 | contributor-4 | feat(zoom) BYO OBF/ZAK — review; ties into self-hosted Zoom story |
| #319 | contributor-1 | fix #80 APIKeyHeader scheme_name — small, mergeable |
| #297 | contributor-4 | fix(vexa-bot) GMeet admission denial — overlaps with #316 pack; coordinate |
| #283 | contributor-3 | fix(msteams) Continue-w/o-AV modal (closes #226) — verify against current Teams DOM, then merge |
| #260 | maintainer | fix(vexa-bot) browser-session exits with node (#258) — own PR; decide land or close |
| #239 | contributor-2 | fix cameraEnabled when voice_agent_enabled (#238) — small fix, has tests pending |
| #179 | contributor-5 | Zoom web playwright merge — stale since March; supersede with #253 epic? |
| #77 | contributor-6 | language detection — very old; either revive or close with reason |

**Pack — PR-merge sweep**
**Why it's here**: community trust + dependency hygiene. Targets #319, #239, #283
as low-risk merges; explicit decisions for the rest.
**Estimated scope**: 1 day reviewer time.

## Issue packs for the next cycle

### Pack — Audit-stage wiring (#303)
**Why it's here**: v0.10.6 was supposed to land this; ran out of cycle time. Audit
scaffold doc exists (`tests3/stages/08-audit.md`); needs:
- `release-audit` Make target
- `stage.py` TRANSITIONS gain `audit` between `validate(green)` and `human`
- `tests3/.claude/skills/audit/` skill bundle
- 1-2 static patterns per category (security, fallbacks, resilience, etc.)
**Repro confidence**: high (well-scoped scaffold work)
**Estimated scope**: 1 day

### Pack — WebM Duration tag (#302 follow-up, Pack U.8 candidate)
**Why it's here**: Pack U.5 server-side finalizer builds valid WebM masters but doesn't
inject Duration into the EBML SegmentInfo. Browsers eventually compute duration by
scanning packets, but show a loading delay before scrubber becomes interactive.
Visible UX latency, especially on short recordings.
**Repro confidence**: high (every recording exhibits it; trivial to repro)
**Estimated scope**: 4 hours (ffmpeg post-process at finalize time + size impact verify)
**Fix sketch**: in `recording_finalizer.py` after byte-concat, run `ffmpeg -i input.webm -c copy -fflags +genpts output.webm` then upload the output. Adds ~1s per finalize.

### Pack — Zoom audio quality (#305)
**Why it's here**: filed today during v0.10.6 ship. Zoom Web recordings sound noticeably
inferior to GMeet/Teams. Architectural — Pack U.4's PulseAudio capture sits below
Zoom's WASM client, so we pull a re-encoded post-AEC mix.
**Repro confidence**: high (every Zoom recording)
**Estimated scope**: research-heavy. Easy first pass: force parecord `--rate=48000 --format=s16le` explicitly. Bigger win: investigate Chrome DevTools Protocol `Audio.startCapture` to bypass PulseAudio entirely.
**Estimated scope**: 1-3 days depending on path chosen

### Pack — broad-except narrowing (#306)
**Why it's here**: audit MAJOR finding from v0.10.6. `callbacks.py:318` swallows
finalizer exceptions broadly with 200-char truncation. Not bitten yet but a class
of future surprises (MemoryError, asyncio.CancelledError, etc.).
**Repro confidence**: low (no current bug; preventive)
**Estimated scope**: 30 min — narrow except clause, add registry check.

### Pack — meeting-api status_change FAILED bypasses classifier (#292)
**Why it's here**: bot-crash branch of meeting-api status_change writes
`completion_reason=null` and stale `failure_stage`. Different code path from Pack C
(user-stop). Re-surfaced during v0.10.6 triage but out of scope.
**Repro confidence**: medium (concrete prod cases listed in #292)
**Estimated scope**: 2-4 hours

### Pack — failure_stage tracker stale at write-time (#294)
**Why it's here**: data.failure_stage is set once at "joining" then never updated
through `awaiting_admission → active`. #276 derives correct value at read-time, so
soft-mitigated but a footgun. Out of scope this cycle.
**Repro confidence**: high (any failure past joining shows wrong stage)
**Estimated scope**: 4-8 hours

### Pack — release-deploy → release-validate plumbing brittleness
**Why it's here**: v0.10.6 cycle 2's gate failed on 5 environmental DoDs that weren't
v0.10.6 product regressions — helm pods-not-settled timing race, compose dashboard
token cache stale after redeploy. Same class kept biting cycle-1 lite-down.
**Repro confidence**: high (reproduces every release-deploy → release-validate cycle)
**Estimated scope**: 1 day. Two specific fixes:
- `lke-setup-helm.sh` / `lke-upgrade.sh`: wait for all pods Ready + 15s DNS settle
- `redeploy-compose.sh`: restart dashboard container after VEXA_API_KEY reseat phase

### Pack — Long-running prod soak validation
**Why it's here**: v0.10.6's 25+ cell autonomous matrix runs 180-240s per cell.
The original v0.10.5.2 chunk-leak only manifested at 24min+. Pack M's confidence
is 75% pre-soak; can be 90%+ with a 25-min real-conversation reproducer run.
**Repro confidence**: medium (need a willing meeting host for a long conversation)
**Estimated scope**: 1 hour to run; 0 to fix if green.

## Backlog cluster sweep (everything else open)

After packing the urgent + Discord-surfaced items above, ~80 open issues
remain. Clustered by theme so the human can pick clusters rather than
individual issues:

### Cluster A — Dashboard / api-gateway / WS reliability
- **#289** api-gateway returns 429 on `GET /meetings`. **Re-triage needed**
  — maintainer says the "list never populates" framing does not match
  current prod behavior. May be stale, partially-fixed, or misdiagnosed.
  Drop from active list until reconfirmed.
- **#269** dashboard WS live status updates intermittently lost after bot
  dispatch (likely silent pubsub hang ref #267).
- **#236** api-gateway `/ws` accepts but doesn't subscribe to Redis pubsub.
- **#299** dashboard Browser view shows wrong meeting when multiple
  parallel meetings are active.
- **#222** admin panel always 401 after successful auth (hosted).
- **#145** interactive bot endpoints not available via api.cloud.vexa.

### Cluster B — K8s / helm deployment maturity
- **#261** K8s `container_id = pod.metadata.uid` makes DELETE a no-op →
  orphan bots forever. **High-impact for k8s self-hosters.**
- **#258** browser-session pod stays Running after node process exits
  (entrypoint keeps container alive for VNC).
- **#273** browser-session: idle pods not GC'd by runtime-api idle_loop.
- **#223** transcription + TTS not working on k8s deployed vexa.
- **#76** Docker Compose GPU on WSL2/RTX4050 — unknown device error.
- Discord-surfaced: helm chart missing transcription-service template
  (customer-G); `customer-C` k8s GET /transcripts returns bot logs.
- Epic-level: **#257** self-hosted operator hardening.

### Cluster C — Long-recording transcription timeouts
- **#243** `transcribe_meeting` httpx 120s timeout insufficient for 2h.
- **#241** post-meeting transcribe gateway timeout for >30s processing
  (blocks 2h recordings).
- **#232** allow user to select model + transcribe meetings >1500s.
- Discord-surfaced: customer-H LiteLLM-proxy use case (already #232).
- Single-pack candidate: "Long-recording / large-meeting transcribe
  pipeline" — ~1 day end-to-end.

### Cluster D — Bot lifecycle: orphans, dedup, DELETE semantics
- **#298** DELETE bot during `requested` state doesn't halt provisioning.
- **#242** POST /bots dedup race — concurrent dispatches create duplicate
  meetings for same `(user, native_meeting_id)`.
- **#233** bot exit incorrectly classified as 'failed' instead of 'completed'.
- **#301** Pack C/J classifier: user-stop during JOINING returns COMPLETED
  but reason=`""` wires status to FAILED.
- **#268** meeting-api recording finalize JSONB write is in chunk-upload
  request path; lost across pod restart.
- Already packed elsewhere: **#292**, **#294**, **#311**, **#313**.
- Epic-level: **#255** bot lifecycle refinement.

### Cluster E — Audio capture quality / zero-segment
- **#237** intermittent zero-segment capture on GMeet + Teams.
- **#204** GMeet ScriptProcessor stops sending audio chunks after join.
- **#115** GMeet bot reaches ACTIVE but `audioTracks=0`.
- **#157** Silero VAD state drift in long-running streaming sessions.
- **#104** repetitions in transcription (Whisper artifact).
- Epic-level: **#251** audio-capture investigation.

### Cluster F — Teams reliability follow-ups
- **#226** Continue-without-AV modal (PR #283 open — review and merge).
- **#124** admitted bot never publishes avatar video.
- **#123** false rejection from overly broad selectors.
- **#133** chat messages show 'typing' but never deliver.
- **#171** Teams admission_false_positive — never actually joins.
- Epic-level: **#252** Teams reliability.

### Cluster G — Speaker / segment reconciliation (research track)
- **#194** fuzzy text matching Whisper ↔ caption.
- **#193** store caption data in DB.
- **#192** caption-driven speaker detection for Teams.
- **#191** MS Teams VAD-based segmentation.
- **#180** speaker activation metadata when transcription is off.
- **#136** hybrid speaker diarization (room + remote).
- **#107** GMeet per-stream audio + speaker mapping research.
- Already packed: **#317** speaker-ID degradation w/ 5+ participants.
- Epic-level: **#256** segment reconciliation research.

### Cluster H — Voice-agent / camera defaults
- **#167** `initVirtualCamera` runs unconditionally even when `voiceAgentEnabled=false`.
- **#168** `voice_agent_enabled` defaults to True — all bots stream avatars.
- **#169** Bot timeout field-name mismatch causes 10s exit.
- **#238** `voice_agent_enabled=true` doesn't set `cameraEnabled` (PR #239 open).
- These look like quick wins (config / default fixes); worth a half-day
  cleanup pack if not already silently fixed in 0.10.x.

### Cluster I — Storage / recording correctness
- **#268** recording finalize JSONB write loses across pod restart (also in D).
- **#282** unable to store recording into AWS bucket (needs-more-info).
- **#224** failed recording after 53 min (needs-more-info).
- **#262** epic: meeting video recording validation + DoDs (depends on #246).

### Cluster J — Architecture / refactor
- **#246** arch: split BotConfig — capability-based model (epic).
- **#245** `SpeakerStreamManager` configurable env vars.
- **#159** configurable data retention (90-day → 7-year).
- **#158** per-meeting RBAC for MCP and API.
- **#79** stale-meeting candidates query parameter.

### Cluster K — Transcription-provider / model
- **#146** can't reach OpenAI transcription via `TRANSCRIBER_URL`.
- **#147** doc `SKIP_TRANSCRIPTION_CHECK` flag + external-provider setup.
- **#155** expand hallucination blocklist (10+ languages, good-first-issue).
- **#156** benchmark Parakeet-TDT vs Whisper.
- **#148** alternative local models (Parakeet, Voxtral, VibeVoice).
- **#149** test GMeet transcript API as alt to bot recording.
- Discord-surfaced: Deepgram, Groq adapter mismatches (already packed).

### Cluster L — Feature requests (low priority, parking)
- **#270** leave when all other users are bots.
- **#190** auto-leave when all participants leave.
- **#189** bot leaves when participants join (regression?).
- **#151** disable fake camera avatar (good-first-issue).
- **#138** screenshot capture for context.
- **#139** real-time LLM decision listener.
- **#131** Ultravox parallel listener.
- **#127** MCP interactive bots tools.
- **#121** capture meeting metadata.
- **#98** authenticated bots.
- **#280** GOOGLE_APPLICATION_CREDENTIALS instead of DB_PASSWORD.
- **#175** docs add @vexaai/transcript-rendering.
- **#194** stale Whisper-caption fuzzy matching (also G).

### Cluster M — Stale / closeable
- **#198** `make all` infinite loop on non-interactive shells (Apr).
- **#166** admission_false_positive in GMeet waiting room (Mar — likely
  superseded by Pack U / 0.10 work).
- **#113** waiting-for-admission state after admission failure (Feb).
- **#128** Zoom returns 201 before guaranteed runtime failure.
- **#96** transcripts hidden when `session_uid` mismatch (likely fixed).
- Recommend a sweep PR or "stale audit" pass to close-with-rationale or
  reconfirm. Saves backlog noise.

## Carry-overs from v0.10.6 triage

From `tests3/releases/260501-chunk-leak/triage-log.md`:
- **Empty `completion_reason` on synthetic-URL failure** — accepted as gap;
  filed for "next cycle's classifier hardening". Pairs with #292/#294/#301.
  Should join the bot-lifecycle classifier pack in v0.10.7.
- **`DEFERRED_TRANSCRIBE_USES_MASTER`** — deferred DoD; needs to be claimed
  in v0.10.7 (ensures `/meetings/{id}/transcribe` uses the new master.webm,
  not a fragment). Pairs with #243/#241 long-recording cluster.

## Suggested approval sets

The shape changed since the 2026-05-03 draft — there are now four hotfix-class
regressions in v0.10.6 that customers are hitting. Recommend splitting the
cycle into a fast hotfix release and a slower follow-on.

### v0.10.6.1 — hotfix (recommended ASAP; ~1–2 days)
1. **#314** Multi-chunk playback first-30s — paying-user breakage
2. **#315 + #308** TTS prod outage / /speak silent — paying enterprise
   customer (customer-A / consulting AI copilot) publicly waiting on hotfix
3. **#313** browser_session DELETE stuck-in-stopping — leak
4. **#311** post_meeting/recording_finalizer master race — correctness
5. ~~#289 dashboard 429~~ **REMOVED** — maintainer (2026-05-08) confirmed the
   issue body's "dashboard never populates" framing is **not** matching
   current prod behavior. Issue may be stale, partially-fixed, or
   misdiagnosed. Action: re-triage #289 against current prod logs before
   pulling into any cycle. Do NOT bundle in v0.10.6.1.

### v0.10.7 — feature + hygiene (recommended; ~1 cycle)
1. **Bot broadcast-surface regression** (Discord-surfaced class regression)
   — bigger than /speak alone; covers image/web/video/HTML/avatar/audio-URL
2. **Audit-stage wiring (#303)** — closes the protocol gap
3. **WebM Duration tag (Pack U.8 / #302 follow-up)** — visible UX win
4. **#316 + waiting-room rejoin** GMeet fast-fail + ~5min eviction retry —
   admission UX + cost
5. **In-repo Discord fetcher** — closes the intake leak (mandated by stage)
6. **broad-except narrow (#306)** — quick hygiene
7. **release-deploy plumbing** — saves triage churn next release
8. **PR-merge sweep** — #319, #239, #283 land; #320, #297, #260, #179, #77 get explicit decisions
9. **(NEW)** **K8s container_id orphan-bot** (#261) — k8s self-host blocker; high-impact for the 1000+ concurrent users from `customer-C`-class deployments
10. **(NEW)** **Long-recording transcribe pipeline** — bundle #243 + #241 + `DEFERRED_TRANSCRIBE_USES_MASTER` from v0.10.6 carry-over; closes the 2h-recording story for paying customers
11. **(NEW)** **Bot-lifecycle classifier hardening** — bundle #292 + #294 + #301 + the v0.10.6 empty-`completion_reason` carry-over; finishes Pack C/J
12. **(NEW)** **Stale-issue audit sweep** — close or reconfirm Cluster M issues (#166, #113, #128, #96, #198) — backlog hygiene, ~2h reviewer time

### Park / research-track (not this cycle)
- **#318** Zoom Web pipeline rewrite (likely supersede via #253 SDK epic)
- **Zoom ASF (#326) + host-busy (#325) reliability** — sibling to #316
  but for Zoom; possibly subsumed by #253 SDK epic
- **#305** Zoom audio quality
- **#317** speaker-ID degradation w/ 5+ participants
- **#292**, **#294** — meeting-api FAILED classifier + failure_stage tracker
- **#321** arm64 Docker images (community; needs maintainer signal — Discord
  also confirms demand from M-series users)
- **#324** 2FA — security epic
- **#322 / #323** hosted regressions — triage first, awaiting reporter
- **#161 promote** — legacy Teams `/l/meetup-join/` URL handling, after #315
  hotfix lands
- **Helm chart parity** (transcription-service template) — bundle with k8s
  audit when it gets attention
- **Adapter docs reality check** — Deepgram + Groq compatibility matrix
- **vexa-lite docs/env hygiene** — `OPENAI_API_KEY`, `MIN_AUDIO_S`, Mac note
- **Better TTS model upgrade** — after /speak delivery is restored

## Open questions for the human picker

1. Hotfix vs single combined release? Hotfix is recommended given #314/#315
   are customer-visible.
2. Discord signal **now folded in** via one-shot fetch (21d window). The
   bigger question stayed: should the in-repo fetcher land in this cycle as
   part of v0.10.7 hygiene, or be parked? Recommend landing — it's small and
   the stage protocol formally requires it.
3. #318 Zoom pipeline — fix forward on Web client, or accelerate the SDK
   epic (#253) and EOL the Web path? Affects effort estimates by ~5x.
4. Worktree bootstrap: the stage state machine still says
   release=`260501-chunk-leak`. Pick a new release id (suggest
   `260508-v0.10.6.1` for the hotfix and `260510-v0.10.7` later) and run
   `make release-worktree ID=<id>` from the main checkout before
   transitioning to `plan`.

Filed under `tests3/releases/_groom-260503-post-v0.10.6/groom.md` (carry-over
location). On approval, rename to `tests3/releases/<chosen-id>/groom.md` and
mark exit condition with `approved: true`.
