# Groom — 260422-zoom-sdk

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| release_id   | `260422-zoom-sdk`                                                      |
| stage        | `groom`                                                                |
| entered_at   | `2026-04-22T15:31:27Z`                                                 |
| actor        | `AI:groom`                                                             |
| predecessor  | `idle` (prior release `260421-prod-stabilize`, shipped 2026-04-21 21:46Z) |
| theme (AI)   | *"Split `zoom` into `zoom-sdk` + `zoom-web` peer platforms; bring `zoom-sdk` to parity with `google_meet`."* |

---

## Scope, stated plainly

0.10.x is stable on helm/compose/lite (as of 260421-prod-stabilize ship).
Market signal is now thin: 72 open issues, 0 new since ship, no Discord
fetcher. This cycle is **not emergent** — it's deliberate architectural
levelling-up of the Zoom platform to match Google Meet's integration bar.

### What "on par with Google Meet" means in this codebase (verified)

`features/realtime-transcription/gmeet/` is **currently** `dods: []` un-gated
(`confidence_min: 0`) — but this is a **refactor oversight**, not a design
choice. Commit `6694502` (2026-04-18) migrated DoDs to sidecar shape and
silently emptied gmeet: the pre-refactor README carried a 6-row DoD table
with real evidence (Bot 135, meeting `rxf-gxis-ozd`, 9 segments, 0% WER,
Chrome 141, confidence 75). That table was never migrated.

Empirical artefacts exist:

- `features/realtime-transcription/gmeet/README.md@6694502^` — 6-row DoD
  table with weights + evidence
- `tests3/testdata/gmeet-compose-260405/pipeline/score.json` — baseline
  metrics: speaker_accuracy=1.0, avg_similarity=0.932, completeness=1.0,
  hallucinations=4, pass=9/9
- `tests3/tests/meeting-tts.sh` — operational shape still in tree
  (recorder + N speakers + TTS ground-truth + transcript scoring)

**So "on par" means both populated from the same evidence**, not both
un-gated. This cycle therefore:

1. Derives zoom-sdk DoDs **from** gmeet's pre-refactor spec, adapted for
   SDK (native raw audio instead of DOM ScriptProcessor; SDK-API speaker
   attribution instead of CSS-class voting).
2. Restores gmeet's own DoDs to its sidecar from the same source.

Concrete surface:

| axis | gmeet shape (post-cycle) | current zoom shape | zoom-sdk after cycle |
|---|---|---|---|
| Feature folder | `features/realtime-transcription/gmeet/{dods.yaml,README.md}` — DoDs **restored** from pre-refactor README (6 items, gate 75) | single `zoom/` for both tracks | `zoom-sdk/` + `zoom-web/` peers |
| DoDs | 6 items (bot-joins, speaker-attrib, content-match, no-halluc, no-missed, selectors-current) + gate 75 | `[]` un-gated | 6 gmeet-derived + 2 SDK-only (per-speaker raw, recording privilege) + gate 75 |
| Platform enum in `meeting-api/schemas.py` | `GOOGLE_MEET = "google_meet"` | `ZOOM = "zoom"` (one value, env `ZOOM_WEB=true` switches path) | `ZOOM_SDK = "zoom_sdk"`, `ZOOM_WEB = "zoom_web"` |
| Platform dir in `services/vexa-bot/core/src/platforms/` | `gmeet/` | `zoom/{native,web}/` + top-level `zoom/index.ts` dispatcher | `zoom-sdk/` + `zoom-web/` peers |
| Dashboard join-form + MCP URL parser | recognises `google_meet` URL shape | recognises single `zoom` | recognises both as peers |
| `smoke-contract` features list in `tests3/test-registry.yaml` | includes `realtime-transcription/gmeet` | neither zoom variant | include `realtime-transcription/zoom-sdk` (web deferred) |
| `tier: meeting` test script | `meeting-tts.sh` (real Meet, bot joins + records + transcript) | none | `meeting-tts-zoom-sdk.sh` (real same-account Zoom) |
| Baseline testdata | `tests3/testdata/gmeet-compose-260405/` (GT + pipeline + score.json) | none | collect on first validate; `tests3/testdata/zoom-sdk-compose-260422/` |
| Self-hosted deploy | Docker-ready out of box | native SDK `libmeetingsdk.so` must be manually sourced; `LD_LIBRARY_PATH` / Qt paths undocumented; no build script | Docker support + `scripts/build-zoom-sdk.sh` + runtime paths set + docs page |
| Known P0 blockers | none | #150 P0 (recording broken) + #128 (201-before-failure) | closed |

### Why SDK-track (not web)?

Both tracks ship today (soft-switched by `ZOOM_WEB=true` env). Web track
has seen active fixes (PR #181, several `fix zoom web …` commits); its
surface is comparable to Meet/Teams Playwright pipelines. **SDK track** is
where the gap lives: recording is broken end-to-end (#150 P0), build
tooling is absent, docs don't exist, and `#128` documents that
`POST /bots platform=zoom` can return `201` with no SDK artifacts present.
A community member hand-built and end-to-end-validated the full fix
(SDK 6.7.2.7020, Ubuntu 22.04, 57.7 s raw audio + speaker-by-name) — the
fix path is **reporter-verified** before we touch a file.

### Why separate peer platforms (not track-under-one-feature)?

Architecture clarity: external meetings via Zoom Marketplace-published SDK
and internal meetings via Playwright-on-zoom.us are different products
(different join paths, different recording paths, different failure modes,
different auth stories, different dependencies — native SDK needs
`libmeetingsdk.so` + Qt libs + system deps; web needs only Playwright).
Collapsing them under one `zoom` feature hides that a `POST /bots
platform=zoom` call can hit either and fail for very different reasons.

**Out-of-scope (explicit, this cycle):**

- `zoom-web` feature body — folder created as a peer, DoDs stay `[]`, code
  moves under `platforms/zoom-web/` but **no behaviour changes**. Next cycle
  populates its story.
- External-meeting publishing (Zoom Marketplace submission) — docs call out
  the Code 63 caveat (SDK app unpublished = same-account only) but
  publishing is a business action, not a code action.
- Zoom web client improvements (#154, #133, etc.) — tracked separately.
- `msteams` live-fire work (#171 consumer-URL admission, #226
  light-meetings modal, #124 avatar video, #133 chat typing) — tracked
  separately. This cycle **does** restore msteams DoDs (Pack I); it does
  **not** fix the underlying Teams admission bugs.
- Cross-repo carry-overs from 260421 (N-2 Grafana, N-3 ValidatingAdmission,
  N-5 pgbouncer activation) — still platform-repo's job.

This is deliberately an architectural cycle, not a fire-drill.

---

## Signal sources scanned

| source                                                              | count | notes                                                              |
|---------------------------------------------------------------------|------:|--------------------------------------------------------------------|
| `gh issue list --state open`                                        |    72 | 0 new since 260421 ship at 2026-04-21T21:46Z                       |
| GH issues matching `zoom` (title or first-page body)                |     2 | **#150** (P0 bundle, 2026-03-05, `good-first-issue,area: bots`) + **#128** (2026-02-16, `area: API`) |
| `git log -- services/vexa-bot/core/src/platforms/zoom/`             |    14 | most recent: `24e0641` (revert experimental opt), `58ba53e` (incremental upload), `33ff781` (PR #181 web client + video) |
| Prior release carry-overs (`260421-prod-stabilize/human-approval.yaml`) | 5 | server-side stitcher (Pack B read-side), 3× cross-repo (vexa-platform), Teams #171 consumer-URL admission — none Zoom |
| `features/realtime-transcription/zoom/`                             |     1 | single folder, `dods: []`, un-gated comment verbatim matches gmeet/msteams |
| `services/vexa-bot/core/src/platforms/zoom/index.ts`                |     — | soft env-switch `ZOOM_WEB=true` already routes web; native SDK is default path |
| Discord                                                             |     — | no in-repo fetcher yet (260421 groom noted same; still future work) |

Signal density is low; architectural goal dominates. The entire pack shape
is driven by **one exhaustive reporter-validated issue (#150)** + **one
API-contract bug (#128)** + **the platform-split decision**.

---

## Packs — candidates for this cycle

All packs land together — this is one coherent story, not a menu.

### Pack F — Platform split (structural, the precondition)  (**recommended: YES, required**)

- **source**: architectural decision (no single issue). Current soft-switch
  in `services/vexa-bot/core/src/platforms/zoom/index.ts:20` (`ZOOM_WEB=true`)
  is invisible to API clients and hides two different failure modes under
  one `platform=zoom` value.
- **scope shape (groom view; plan pins DoDs)**:
  - **Platform enum** (`services/meeting-api/meeting_api/schemas.py:194-211`):
    add `ZOOM_SDK = "zoom_sdk"` + `ZOOM_WEB = "zoom_web"` alongside or
    replacing `ZOOM = "zoom"`. Plan decides migration story (alias old →
    sdk? require new? deprecate with warning?).
  - **Feature folders**: `features/realtime-transcription/zoom/` →
    `zoom-sdk/` + `zoom-web/`. Both inherit gmeet's un-gated pattern verbatim:
    `dods: []`, `confidence_min: 0`, same placeholder comment. `README.md`
    in each says which code dir it owns.
  - **Code dirs**: `services/vexa-bot/core/src/platforms/zoom/native/` →
    `platforms/zoom-sdk/`; `platforms/zoom/web/` → `platforms/zoom-web/`.
    The dispatcher `zoom/index.ts` retires; two separate entry points
    (`handleZoomSdk`, `handleZoomWeb`) hook straight off the platform
    value. Env-switch `ZOOM_WEB=true` no longer needed.
  - **Callers to update** (meeting-api, mcp, dashboard, vexa-agent, runtime-api):
    all places currently dispatching on `platform == "zoom"` now dispatch
    on `zoom_sdk` OR `zoom_web` (grep already cataloged — see Signal table).
    Includes `run-zoom-bot.sh` entrypoint shell in `services/vexa-bot/`.
  - **`tests3/test-registry.yaml` smoke-contract features list**: add
    `realtime-transcription/zoom-sdk`. `zoom-web` stays absent this cycle
    (matches deferred posture).
- **estimated scope**: ~15–20 touched files; mostly renames + enum
  expansion + callsite updates. **~1 day** including migration decisions.
- **repro confidence**: N/A — mechanical refactor.
- **owner feature(s)**: new `realtime-transcription/zoom-sdk` + new
  `realtime-transcription/zoom-web` (empty peer).
- **open questions for plan**:
  - Migration: alias `zoom` → `zoom_sdk` for N cycles? Hard-break? Accept
    both, prefer new, log deprecation on old?
  - Does the MCP URL parser need two different URL shapes
    (`zoom.us/j/<id>` vs `zoom.us/wc/…` web-client) or one that dispatches
    by user preference?

### Pack A — SDK native recording works end-to-end (P0 of #150)  (**recommended: YES, P0**)

- **source**: [#150](https://github.com/Vexa-ai/vexa/issues/150), reporter
  (bockage → Vexa team) validated **every fix below** on a live test VM
  with SDK 6.7.2.7020: 57.7 s raw audio captured, speakers detected by
  name, participant detection, active-speaker events firing. "Everything
  below is validated" quoted from the issue body.
- **severity**: **P0 / feature broken.** Self-hosters who build the native
  addon today cannot record. Without Pack A, the SDK track is unusable.
- **scope shape** (files fixed all under the new `platforms/zoom-sdk/`):
  - **A.1** `native/src/zoom_wrapper.cpp` — fix `StartRawRecording` flow.
    Currently `StartRecording` calls `audioHelper_->subscribe()` directly
    → returns `SDKERR_NO_PERMISSION` (code 12). Correct flow:
    `recordingCtrl_->CanStartRawRecording()` → `StartRawRecording()` →
    `audioHelper_->subscribe(&delegate)`. Also call
    `RequestLocalRecordingPrivilege()` first if `CanStartRawRecording`
    returns NO_PERMISSION (host auto-approves if account configured).
    Adds: `#include "meeting_service_components/meeting_recording_interface.h"`,
    `IMeetingRecordingController* recordingCtrl_` member.
  - **A.2** `native/src/zoom_wrapper.cpp` — implement
    `onOneWayAudioRawDataReceived(AudioRawData*, uint32_t)` (currently
    no-op). Forward per-user audio buffers to JS with user_id, same shape
    as existing `onMixedAudioRawDataReceived`. Unblocks per-speaker
    transcription attribution beyond timing correlation.
  - **A.3** `sdk-manager.ts` — add recording-privilege retry loop:
    `RequestLocalRecordingPrivilege()` → wait for approval → poll
    `CanStartRawRecording()` every ~2 s → once granted, call
    `StartRawRecording()` + `subscribe()`. User-visible log line
    "Waiting for recording permission from host…".
  - **A.4** `strategies/join.ts` — register `onActiveSpeakerChange` AFTER
    `joinAudio()`, not before. `audioController_` is only set inside
    `joinAudio()`; registering earlier produces "Audio controller not
    available for speaker events".
  - **A.5** `entrypoint.sh` / Docker CMD — set `LD_LIBRARY_PATH` to
    include the **nested** `qt_libs/Qt/lib/` path (not just `qt_libs/`)
    BEFORE system Qt. Without this every invocation dies with
    `undefined symbol: _ZNSt28__atomic_futex_unsigned_base…, version Qt_5`.
- **estimated scope**: 2 C++ files, 2 TS files, 1 entrypoint edit. Most
  of the patch exists in the issue body. **~1–1.5 days** including local
  build verification. Actual recording validation happens in tier-meeting
  stage (Pack G).
- **repro confidence**: **HIGH.** Issue body contains the exact C++
  include, exact retry-loop shape, exact `LD_LIBRARY_PATH` string,
  reporter-verified on Ubuntu 22.04 + Node v22.14.0 + SDK 6.7.2.7020.
- **owner feature(s)**: `realtime-transcription/zoom-sdk` (new).

### Pack B — SDK build + Docker + `.env` parity (P1/P2 of #150)  (**recommended: YES**)

- **source**: #150 P1 sections 3, 8, 9 + P2 section 7.
- **symptom**: new contributors can't get the SDK track running. There's
  no build script, Docker doesn't include the SDK, `.env.example` says
  nothing about Zoom vars, and `ensureSdkAvailable()` emits a generic
  error for every failure mode.
- **scope shape**:
  - **B.1** `scripts/build-zoom-sdk.sh` (new). Contract:
    - Check SDK files (`libmeetingsdk.so`, `qt_libs/`); give
      download-instruction error if missing.
    - Install system deps: `apt-get install -y qtbase5-dev libxcb-xtest0`.
    - `npm install node-addon-api`; `npx node-gyp rebuild`.
    - Validate build: `node -e "require('./build/Release/zoom_sdk_wrapper')"`.
    - Print success + next-steps.
  - **B.2** `services/vexa-bot/Dockerfile`: add `qtbase5-dev`,
    `libxcb-xtest0` apt deps; bake the `LD_LIBRARY_PATH` into
    `ENTRYPOINT`/`CMD`; document a volume mount or build step for the
    proprietary SDK files (they can't be redistributed in the image).
  - **B.3** `.env.example` (repo root or deploy/compose): add
    `ZOOM_CLIENT_ID=` + `ZOOM_CLIENT_SECRET=` with a comment tagging them
    optional-for-Zoom-SDK.
  - **B.4** `sdk-manager.ts::ensureSdkAvailable()` — detect and map
    specific failures to fix suggestions: missing `libmeetingsdk.so`, Qt
    symbol undefined, missing `libxcb-xtest0`, auth failure (client-id/secret),
    code 63 (external-meeting requires Marketplace publish), code 12
    (local-recording setting not enabled). Table-driven ~30-40 LOC.
- **estimated scope**: 1 new shell script, 1 Dockerfile patch, 1 env
  file, 1 TS error-mapping function. **~0.5 day.**
- **repro confidence**: HIGH — all 6 failure modes in the table are
  reporter-encountered, with exact symptom strings.
- **owner feature(s)**: `realtime-transcription/zoom-sdk` +
  `infrastructure` (Dockerfile).

### Pack C — SDK self-hosted docs  (**recommended: YES**)

- **source**: #150 "Documentation needed" section.
- **symptom**: no docs page for Zoom SDK self-hosting exists. The
  community workflow reconstructed from #150 is the closest thing to
  a guide, and it lives inside a GH issue.
- **scope shape**: new page "Zoom SDK (Native) Platform Setup" covering:
  1. Create Meeting SDK app on marketplace.zoom.us (Meeting-SDK app
     type; client-id + client-secret).
  2. Download the SDK (proprietary, not redistributable). Target layout
     under `services/vexa-bot/core/src/platforms/zoom-sdk/native/zoom_meeting_sdk/`:
     `libmeetingsdk.so` (+ symlink `.so.1`), `libcml.so`, `libmpg123.so`,
     `qt_libs/Qt/lib/…`.
  3. Build native addon: `apt-get install qtbase5-dev libxcb-xtest0 && cd
     services/vexa-bot && npm install node-addon-api && npx node-gyp
     rebuild` — OR `scripts/build-zoom-sdk.sh` from Pack B.
  4. Runtime config: env vars, `LD_LIBRARY_PATH` preserve-order, create
     `~/.zoomsdk/logs/`.
  5. Zoom-account settings: Recording → "Record to computer files" ON;
     "Auto approve permission requests" for both internal and external
     participants ON.
  6. Limitations: unpublished SDK apps = same-account only (Code 63);
     Marketplace publishing required for external meetings; binaries are
     proprietary.
- **where it lives**: open question — plan decides between in-repo
  (`services/vexa-bot/docs/zoom-sdk-setup.md` referenced from main
  README) and `docs.vexa.ai` (cross-repo; matches gmeet's approach).
  Recommendation: **in-repo at `services/vexa-bot/docs/zoom-sdk-setup.md`**;
  `docs.vexa.ai` cross-publication is a follow-on issue.
- **estimated scope**: 1 markdown file, ~200-300 lines. **~0.5 day.**
- **repro confidence**: HIGH — #150 already has the entire guide written
  in issue body.
- **owner feature(s)**: `realtime-transcription/zoom-sdk`.

### Pack D — API-contract pre-flight for `zoom_sdk` (#128)  (**recommended: YES**)

- **source**: [#128](https://github.com/Vexa-ai/vexa/issues/128),
  2026-02-16. `POST /bots` with `platform=zoom` returns `201 Created`
  even when `services/vexa-bot/build/Release/zoom_sdk_wrapper.node` or
  `services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so`
  is missing. Meeting transitions `requested → joining → failed` within
  seconds; failure only surfaces later via `GET /meetings.data.error_details`.
- **severity**: MEDIUM — API-contract bug. Known prerequisites at request
  time should synchronously gate the 201.
- **scope shape**:
  - On `POST /bots` with `platform=zoom_sdk` (the new enum value — **only
    `zoom_sdk`, not `zoom_web`**), run a pre-flight that checks both
    artifacts are present in the bot-runner image/container.
  - If missing: structured error response (`503` or `412 Precondition
    Failed` — plan picks), body `{"detail": {"code": "zoom_sdk_not_available",
    "missing_artifacts": [...]}}`. Meeting row **not** created.
  - If present: existing code path unchanged.
  - `zoom_web` path (no native deps) stays 201-immediate — no change.
- **estimated scope**: ~1 helper function + 1 early check in the meeting
  create handler. **~0.25 day.**
- **repro confidence**: HIGH — #128 has the exact shell repro + response
  body.
- **owner feature(s)**: `realtime-transcription/zoom-sdk` +
  `bot-lifecycle`.

### Pack E — Populate `zoom-sdk` DoDs from gmeet's pre-refactor spec  (**recommended: YES**)

- **source**: `features/realtime-transcription/gmeet/README.md@6694502^`
  (pre-refactor DoD table) + `tests3/testdata/gmeet-compose-260405/pipeline/score.json`
  (empirical baseline: speaker_accuracy=1.0, avg_similarity=0.932,
  completeness=1.0, hallucinations=4, pass=9/9) + `tests3/tests/meeting-tts.sh`
  (operational shape).
- **scope shape**:
  - Create `features/realtime-transcription/zoom-sdk/{dods.yaml,README.md}`.
    README mirrors the gmeet pre-refactor body, adapted for SDK (native
    `StartRawRecording` + per-user `onOneWayAudioRawDataReceived` instead
    of DOM ScriptProcessor + CSS-class voting; `onActiveSpeakerChange` +
    `getUserInfo` instead of speaker-identity voting lib).
  - `dods.yaml` — gate `confidence_min: 75` (matches gmeet pre-refactor
    number — accounting for parallel classes of edge cases). 8 items,
    weights sum to 100. Plan will pin exact evidence-shape strings; groom
    locks the list + weights:

    | # | id | weight | derived from gmeet DoD # | SDK adaptation |
    |---|---|--:|---|---|
    | 1 | zoom-sdk-bot-joins-and-captures-raw-audio | 20 | 1 (bot joins + per-speaker audio) | native SDK `StartRawRecording` + `onMixedAudioRawDataReceived` fires ≥1 buffer within 30s of active |
    | 2 | zoom-sdk-speaker-attribution-correct | 20 | 2 (correct speaker attributed) | via `onActiveSpeakerChange` + `getUserInfo`; threshold: `score.json:speaker_accuracy >= 0.90` |
    | 3 | zoom-sdk-content-matches-ground-truth | 20 | 3 (≥70% similarity) | Whisper pipeline downstream is identical; threshold: `score.json:avg_similarity >= 0.70` |
    | 4 | zoom-sdk-no-hallucinated-segments | 8 | 4 (0 output lines without GT match) | threshold: `hallucinations == 0`. **NB: gmeet historical baseline is 4 — plan must reconcile (tighten-for-zoom or match-gmeet-actual); leaning match-actual: `<= 4 per 9 GT lines`** |
    | 5 | zoom-sdk-no-missed-gt-lines | 10 | 5 (completeness 100%) | threshold: `completeness == 1.0; missed == 0` |
    | 6 | zoom-sdk-sdk-api-current | 7 | 6 (DOM selectors current) | tier=contract; `node -e "require('./build/Release/zoom_sdk_wrapper')"` exits 0; JWT auth returns valid; SDK version pinned in meta |
    | 7 | zoom-sdk-per-speaker-raw-audio-forwarded | 8 | (no gmeet analogue; SDK-only) | `onOneWayAudioRawDataReceived` body in `native/src/zoom_wrapper.cpp` forwards `user_id + pcm` to JS (not no-op). tier=static grep + tier=meeting evidence: segments attributed per-user from raw-audio buffer, not only timing correlation |
    | 8 | zoom-sdk-recording-privilege-granted | 7 | (no gmeet analogue; SDK-only, Zoom permission model) | tier=meeting log: `RequestLocalRecordingPrivilege → granted within 10s → CanStartRawRecording=true → StartRawRecording succeeds` |

  - Also create `features/realtime-transcription/zoom-web/{dods.yaml,README.md}`
    — **empty peer**, mirrors current gmeet empty shape (`dods: []`,
    gate 0, placeholder comment). Web-track DoD population deferred.
  - Retire `features/realtime-transcription/zoom/` (git-mv into
    `zoom-sdk/` — history preserves via that side; `zoom-web/` is new).
- **estimated scope**: ~0.5 day. The 8 items are derived lock-for-lock
  from gmeet's pre-refactor table; plan pins the evidence-shape strings
  and check types (grep / script / aggregate).
- **repro confidence**: HIGH for items 1–5 (gmeet pre-refactor evidence
  table + live `score.json` baseline); HIGH for 6 (contract-tier static
  check); HIGH for 7–8 (reporter-validated in #150).
- **owner feature(s)**: new `realtime-transcription/zoom-sdk` +
  `realtime-transcription/zoom-web` (empty).
- **open question for plan**: item-4 threshold — tighten to `0`
  (unforgiving), match gmeet-actual `<= 4 per 9`, or use a rate
  `hallucination_rate <= 0.45 = 4/9`? Recommend rate; makes the DoD
  scale with GT length.

### Pack H — Restore `gmeet` DoDs to sidecar (refactor oversight)  (**recommended: YES**)

- **source**: commit `6694502` "refactor step 2: DoDs → features/<name>/dods.yaml
  sidecars" (2026-04-18) migrated DoDs shape and silently emptied gmeet's
  sidecar — the 6-row DoD table in the pre-refactor README was never
  carried over. This is a **refactor oversight**, not a design choice:
  the commit message claims "12 features had no machine-readable DoDs"
  but gmeet's were machine-readable (in README body, not frontmatter).
- **why bundle with zoom-sdk parity cycle**: the *definition* of "par
  with gmeet" is ambiguous until gmeet's DoDs are populated. Pack E
  derives zoom-sdk items from the same pre-refactor content; shipping
  zoom-sdk DoDs while gmeet stays empty is literally inverted parity
  (zoom-sdk would be gated, gmeet un-gated). 30-minute fix closes the
  ambiguity permanently.
- **scope shape**:
  - `features/realtime-transcription/gmeet/dods.yaml` — populate with
    the same 6 items as Pack E items 1-6 (identical definitions; the
    SDK adaptations in Pack E items 2/6 fold back to gmeet-native shape:
    speaker attribution via DOM voting + selectors-current via grep on
    selectors.ts). Gate `confidence_min: 75` (matches the pre-refactor
    confidence value verbatim).
  - `features/realtime-transcription/gmeet/README.md` — restore the
    "Why / What / Speaker identity / Key files / Key selectors / How /
    DoD" body from `6694502^`. Current README is 7 stub lines — replace
    with the 90-line pre-refactor content. Frontmatter adjusted to
    match current sidecar schema (strip `tests3.targets/checks`; keep
    `services` block).
  - `tests3/testdata/gmeet-compose-260405/pipeline/score.json` already
    exists as empirical baseline; Pack H does not touch it.
  - No behaviour change in gmeet runtime code. Only the contract
    declaration is restored.
- **estimated scope**: ~0.25 day. Pure content-restoration from git
  history + current-schema alignment.
- **repro confidence**: N/A (declarative).
- **owner feature(s)**: `realtime-transcription/gmeet` (restoration).
- **side-effect warning**: restoring gate to 75 means
  `realtime-transcription/gmeet` will gate real runs. Plan must verify:
  (a) the `meeting-tts.sh` test still scores ≥75 against the current
  Whisper pipeline (recent transcription changes landed in 260419/260421
  may have moved the number); (b) if actual score has regressed, either
  fix the regression OR lower the gate — but never both silently drop
  the DoDs again.

### Pack I — Restore `msteams` DoDs to sidecar (same refactor oversight)  (**recommended: YES**)

- **source**: commit `6694502` (same as Pack H) silently emptied msteams's
  DoD sidecar. Pre-refactor README carried an **8-row DoD table** with
  evidence across compose/helm/lite modes dated 2026-04-09. Empirical
  baseline exists: `tests3/testdata/teams-compose-260405/pipeline/score.json`
  — speaker_accuracy=0.8, avg_similarity=0.932, completeness=0.8,
  hallucinations=40, pass=16/20.
- **why bundle with zoom-sdk parity cycle**: consistency. Pack H
  restores gmeet's DoDs to close the parity-definition loop for zoom-sdk.
  Leaving msteams's empty is the same oversight with the same fix.
  Restoring all three together (gmeet, msteams, zoom-sdk) means the
  `realtime-transcription/*` subtree is definitionally coherent after
  this cycle, not two-thirds-coherent.
- **scope shape**:
  - `features/realtime-transcription/msteams/dods.yaml` — populate with
    the 8 items from the pre-refactor README, as-is. Gate
    `confidence_min: 75` (verbatim). Items include:

    | # | id | weight | check |
    |---|---|--:|---|
    | 1 | msteams-bot-joins-with-passcode-and-captures-mixed-audio | 15 | joined + mixed-audio stream captured |
    | 2 | msteams-speaker-attribution-correct | 25 | caption-author-driven attribution |
    | 3 | msteams-content-matches-ground-truth | 25 | ≥70% similarity |
    | 4 | msteams-no-missed-gt-lines-under-stress | 10 | 20+ utterances; gmeet-style completeness gate |
    | 5 | msteams-no-hallucinated-segments | 5 | 0 output without GT match (pre-refactor status PARTIAL — bug #24 whisper-hallucination-on-silence still present; see plan-note below) |
    | 6 | msteams-speaker-transitions-no-content-lost | 10 | content preserved across speaker switches |
    | 7 | msteams-url-formats-parsed | 10 | T1–T6 URL variants; ties into existing static checks `TEAMS_URL_{STANDARD,SHORTLINK,CHANNEL,ENTERPRISE,PERSONAL}` |
    | 8 | msteams-overlapping-speech-both-captured | 5 | pre-refactor status SKIP; leave as SKIP/weight-0 or drop-to-0 pending capability |

  - `features/realtime-transcription/msteams/README.md` — restore the
    ~120-line pre-refactor body (Why / What / Caption-driven speaker
    boundaries / Key files / Differences-from-Meet / How / DoD / Known
    issue B11). Frontmatter adjusted to current sidecar schema.
  - `tests3/testdata/teams-compose-260405/pipeline/score.json` already
    exists; Pack I does not touch it.
- **estimated scope**: ~0.25 day. Pure content-restoration (analogous to
  Pack H).
- **repro confidence**: N/A (declarative).
- **owner feature(s)**: `realtime-transcription/msteams` (restoration).
- **known-fire warning — plan must reconcile**: the pre-refactor confidence
  value is 75, but:
  - Item 5 (no hallucinations): bug #24 explicitly unresolved at pre-refactor
    time; current `score.json` shows 40 hallucinations per 20 GT lines (rate 2.0).
  - Items 4 + 8: SKIP in pre-refactor — plan picks ACCEPT-AS-SKIP
    (weight 0) or test-this-cycle.
  - #171 (consumer-URL admission exit 137) + #226 (light-meetings modal)
    are **present-day** live fires — item 1 may not pass on the current
    build against consumer URLs.
  - Plan must: (a) decide which items weight=0 or SKIP for *this* restoration;
    (b) flag which DoDs are expected to **fail** on current code, and
    whether that becomes a regression target or stays as a known-fail
    (evidence_shape: "expected_fail pending #171 / #226").
  - This reconciliation is explicitly plan's job; groom just restores
    the list.
- **side-effect warning**: restoring gate to 75 means `realtime-
  transcription/msteams` will gate real runs. If current msteams actual
  score is below 75 (hallucinations rate 2.0, Teams admission bugs),
  validate will go RED on the Teams path. Plan may choose to: (a) ship
  at gate 75 + accept RED until #171/#226/#24 land → forces a
  stabilization cycle next; (b) ship at a LOWER gate (e.g. 50) with
  a TODO-note to lift after Teams stabilizes. **Do not silently empty
  again.**

### Pack G — Smoke-contract + tier-meeting test parity with gmeet  (**recommended: YES**)

- **source**: architectural (gmeet has `meeting-tts.sh` as its
  `tier: meeting` test under `realtime-transcription/gmeet`).
- **scope shape**:
  - `tests3/test-registry.yaml` — add `realtime-transcription/zoom-sdk`
    to `smoke-contract.features`. (This registers it for static-tier
    contract checks that run on every lite/compose/helm smoke.)
  - `tests3/tests/meeting-tts-zoom-sdk.sh` (new) — mirror
    `meeting-tts-teams.sh` structure. Entries in registry:
    `tier: meeting`, `runs_in: []`, `features:
    [realtime-transcription, realtime-transcription/zoom-sdk]`.
    Uses a real Zoom same-account meeting (with SDK + account-settings
    configured per Pack C). Asserts: bot joins, admission reports true,
    recording produces chunks in MinIO, transcript persists, bot exits
    cleanly. Same shape as `meeting-tts.sh`.
  - The `tier: meeting` test **does not run on every validate** — it's
    runner-scoped (matches gmeet/teams today). Running it is a separate
    operator action during human eyeroll.
- **estimated scope**: 1 new test script, 2-3 lines in test-registry.yaml.
  **~0.5 day** (shell-wrangling + dry-run against a real Zoom meeting is
  nontrivial).
- **repro confidence**: HIGH for the structure; the meeting itself
  requires live Zoom credentials on the test runner, same as gmeet.
- **owner feature(s)**: `realtime-transcription/zoom-sdk`.
- **open question for plan**: does the Zoom SDK same-account limitation
  (Code 63) force the test account to own the meeting? Yes — the test
  runner's Zoom account must host the meeting it tests against.

---

## Suggested cycle shapes — human picks

### Shape 1 — Full parity  (**my recommendation**)

- Pack F (platform split)
- Pack A (native recording P0)
- Pack B (build + Docker + `.env`)
- Pack C (docs)
- Pack D (#128 pre-flight for `zoom_sdk` only)
- Pack E (zoom-sdk DoDs populated from gmeet pre-refactor spec)
- Pack H (gmeet DoDs restored — closes the parity-definition loop)
- Pack I (msteams DoDs restored — same refactor oversight)
- Pack G (smoke-contract + tier-meeting test)

**Total**: ~4.25 days develop + validate + human. Delivers the whole
"zoom-sdk on par with gmeet" bar literally — all three real-time
transcription features gated at 75, items derived from the same
evidence. The `realtime-transcription/*` subtree becomes definitionally
coherent after this cycle. `zoom-web` gets the platform-split free ride
(its body remains untouched for a later cycle).

This is the recommended shape because: (a) #150 P0 alone isn't
deployable without Pack B (users still can't build it); (b) without the
platform split (F), the API surface still lies about what's breaking;
(c) without E+H+I the word "parity" is meaningless (zoom-sdk can't be
"on par" with features that themselves have no DoDs); (d) without G the
tests3 system can't tell if Zoom SDK regressed; (e) Pack D is a 0.25-day
hygiene win that pairs naturally with F; (f) Pack I is 0.25 day and
completes the DoD-restoration symmetry — leaving one of three un-gated
invites the same drift-to-empty to recur.

**Plan-time reconciliation warning for I**: msteams restored gate 75
may go RED on current code (Teams has live-fire bugs #171 + #226 + #24
whisper-hallucination-on-silence). Plan chooses whether to ship at 75
(forces a Teams-stabilize cycle next) or ship at a lower gate with an
explicit TODO. See Pack I "known-fire warning" for the decision list.

### Shape 2 — Code-only, docs-deferred

- Pack F + A + B + D + E + H + I + G. **Drop** Pack C to a follow-on
  "zoom-sdk docs" issue after parity lands.

**Total**: ~3.75 days. Risk: self-hosters can build + run but setup
discovery is still "read #150 issue body". Parity in contract-terms is
met; user-facing parity (findable docs) is not. Not strongly recommended
— docs is a half-day.

### Shape 3 — Code-only, DoDs-deferred

- Pack F + A + B + C + D + G (drop E + H + I).

**Total**: ~3 days. Risk: zoom-sdk works end-to-end but "parity with
gmeet" remains aspirational — all three `realtime-transcription/*`
features stay un-gated, and the next cycle has to revisit the
parity-definition question. **Not recommended.** The entire premise of
this cycle is closing the parity gap; shipping without the DoDs leaves
the definitional work undone.

### Shape 4 — Split + unblock-P0 + DoDs only

- Pack F + A + E + H + I + G. **Drop** B + C + D.

**Total**: ~2.75 days. Recording works (A); platforms are peers (F);
DoDs closed (E+H+I); registry/tests know (G); build toolchain + docs +
#128 left open. Contract-parity achieved but deployability suffers.

Not recommended: Pack B is the difference between "a Vexa dev can run
it" and "any self-hoster can". Pack C makes that delta public-facing.
For a parity cycle it's non-negotiable.

### Shape 5 — Minimum-viable parity gesture

- Pack F + E + H + I + G only. **Drop** all code fixes; just reshape
  registry + feature folders + enum + populated DoDs.

**Total**: ~1.75 days. Contract-parity only: all three features gated
from the same evidence, but zoom-sdk actual behavior can't pass its gate
(recording broken per #150), and msteams may not pass its (Teams bugs).
The DoDs would trip on first validate. **Do not recommend.** Only makes
sense as an inert-declaration cycle paired with a separate code-fix
cycle.

---

## Halt — waiting on human pack approval

Groom's contract: present packs, human picks at least one. Per stage
contract, this groom does not write `scope.yaml` (that's `plan`). The
advance is:

```bash
make release-plan ID=260422-zoom-sdk
```

which transitions `.current-stage` → `plan` and scaffolds
`scope.yaml` + `plan-approval.yaml`.

### Approvals

One approval per pack. Human may approve a subset.

```yaml
packs:
  F_platform_split:                { approved: true }
  A_sdk_recording_p0:              { approved: true }
  B_build_docker_env:              { approved: true }
  C_sdk_docs:                      { approved: true }
  D_api_preflight_zoom_sdk:        { approved: true }
  E_zoom_sdk_dods_from_gmeet_spec: { approved: true }
  H_gmeet_dods_restored:           { approved: true }
  I_msteams_dods_restored:         { approved: true }
  G_smoke_and_meeting_test:        { approved: true }

recommended_shape: 1   # Full parity (F+A+B+C+D+E+H+I+G)
shape_picked: 1        # human: "let's go" — 2026-04-22T15:47Z
slug_confirmed: "260422-zoom-sdk"
approver: dmitry@vexa.ai
approval_signal: "let's go" (in-turn reply to Shape-1 recommendation)
approval_addendum: "add msteams dods restore as well if not present" — 2026-04-22T15:52Z
```

Per stage contract and CLAUDE.md, AI may not flip any `approved: true`
on its own. Human says so in the current turn, then plan scaffolds.
