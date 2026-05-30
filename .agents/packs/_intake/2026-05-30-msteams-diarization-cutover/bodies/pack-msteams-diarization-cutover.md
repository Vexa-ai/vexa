# Pack Epic: MS Teams diarization cutover — replace caption-driven audio gating with pyannote segmentation + blue-square correlation

## CEO outcome

Vexa's MS Teams transcripts no longer break when Teams' own caption stream
lags, flickers, or doesn't fire. Speaker attribution is driven by audio
content (the bot's own diarization model) and **correlated** with the Teams
DOM "blue-square" speaker indicator for identity — so when a customer
watches their meeting transcript, the speaker boundaries match what they
actually heard, including interruptions, overlap, and brief interjections
that Teams' captions miss. Same model + same architecture is reusable for
Google Meet and Zoom later, but this pack ships the MS Teams cutover only.

## CTO outcome

The MS Teams audio path in `services/vexa-bot/core/src/index.ts` switches
from **caption-driven boundary control** (the `lastCaptionSpeakerId` flush
mechanism in `handleTeamsCaptionData`) to **diarizer-driven boundary
control** (pyannote/segmentation-3.0 + wespeaker, validated in
`pack-msteams-local-diarization-rnd` at boundary recall 90.4%, transcript
quality 91.9%, balanced score 79.9% across a 9-corpus eval suite).
Captions become an **identity hint** the new `TeamsAttributor` correlates
against diarizer commits. The cutover is **complete**: no fallback path,
no env-flag toggle, no shadow mode — the caption-driven flush mechanism
is deleted. The same hallucination-defense layer (audio gate + Whisper
confidence gates + bot's existing phrase filter) lands in production.

## User outcome

End users of the dashboard:

- see speaker labels switch at the right moment (±50ms typical, vs the
  current ~1.5s caption lag);
- no more "Amen.", "Thanks for watching", "What?" hallucinations injected
  by Whisper when fed sub-threshold or short audio (three independent
  filter layers catch these);
- brief interjections ("Right.", "Yeah.", "100%") that Teams captions
  drop entirely are now attributed correctly because the diarizer hears
  them in the audio;
- in overlap (two speakers talking), the dominant speaker is attributed
  per commit and the other speaker's audio is no longer silently leaked
  into the dominant speaker's transcript bucket.

## Included raw issues / PRs

This pack delivers the production cutover of the research validated in:

- Predecessor RnD pack: `pack-msteams-local-diarization-rnd`
  - GitHub issue: [Vexa-ai/vexa#378](https://github.com/Vexa-ai/vexa/issues/378)
  - PR: [Vexa-ai/vexa#379](https://github.com/Vexa-ai/vexa/pulls/379)
  - Branch: `codex/pack-pack-msteams-local-diarization-rnd`
  - Notable commits in the predecessor branch that this pack ports to
    production:
    - `db7e8f9` — live pyannote/segmentation-3.0 integration in `OnnxLocalDiarizer`
    - `ac9eeeb` — pyannote/segmentation-3.0 A/B vs wespeaker boundary recall
    - `a89a1e9` — hallucination layer (silence-filter + min-speech gate)
    - `b67d1a9` — Whisper confidence-based hallucination gates
    - `815fdca` — tightened hallucination gates + 4s cluster cooldown

No NEW raw issues are claimed by this pack — the work is the productisation
of the predecessor's design decisions.

## Explicitly out of scope

- **Google Meet** and **Zoom** platform ports. Their audio pipelines
  differ (GMeet has per-track WebRTC audio with platform-native speaker
  attribution; Zoom uses PulseAudio + DOM polling). Each is a separate
  future pack.
- **Same-gender 2-speaker corpus regression** (eval shows pyannote
  −22.2 pts on `2males-overlap` vs wespeaker baseline). The structural
  fix needs an embedding-based segment refinement pass on top of
  pyannote. Future pack.
- **Speaker rename UI** — the `updateSpeakerName` path is repurposed
  here for late-arriving caption resolution; the dashboard's existing
  rename rendering is reused as-is.
- **Removing the RnD pack** — `services/vexa-bot/rnd/diarization/` stays
  as the iteration sandbox + ground-truth eval suite. Production
  imports the new `services/vexa-bot/core/src/services/diarization/`
  module, not the RnD one.
- **Replacing wespeaker** — wespeaker stays as the per-segment
  embedding + clustering model. Only the **change-point detector** is
  replaced by pyannote.

## Blast radius

**Production impact**: any bot session that joins an MS Teams meeting
after deploy goes through the new path. Other platforms unaffected.

**Files modified in `services/vexa-bot/core/src/`**:

- `index.ts` — `handleTeamsAudioData` signature change (drop
  `speakerName` parameter); deletion of `lastCaptionSpeakerId` /
  `flushSpeaker` block in `handleTeamsCaptionData`; new diarizer +
  `TeamsAttributor` plumbing in the per-speaker pipeline init.
- `services/diarization/` (new directory) — ports of
  `onnx-local-diarizer.ts`, `pyannote-segmenter.ts`,
  `online-clustering.ts`, `metrics.ts` from the RnD pack.
- `services/teams-attributor.ts` (new file) — caption-event correlator
  + per-commit best-candidate speaker resolution + late-caption
  rename trigger.
- `platforms/msteams/recording.ts` — callsite of `handleTeamsAudioData`
  drops the speaker-name argument.
- `services/audio-capture/*` (AudioWorklet code, browser context) —
  63-tap windowed-sinc lowpass added before the 48k→16k decimation,
  fixing the aliasing bug surfaced in the RnD pack.

**Image size**: ~32 MB growth from baking the two ONNX models
(`pyannote/segmentation-3.0`: 6.6 MB; `wespeaker-voxceleb-resnet34-LM`:
~25 MB) into the bot's Docker image at build time.

**Runtime cost per bot**: pyannote inference ~50 ms every 500 ms (≈10%
of one core); wespeaker embedding ~64 ms per commit (peak ~20% during
heavy turn-taking). Combined steady-state ~10–30% of one core average.
Memory: +~100 MB per bot.

**Latency cost**: each commit is buffered ~2.5–3 s before publish to
allow Teams captions to catch up; if no caption arrives in window,
publish with the diarizer's cluster ID and `updateSpeakerName` later
when caption resolves. The dashboard's existing rename path handles
this without changes.

## Data / schema / API / public-contract decisions

- **No wire-format changes** to the Redis pub/sub `tc:meeting:<id>:mutable`
  channel (the bot's `SegmentPublisher` payload shape stays the same).
- **No DB schema changes**.
- **No public API changes**.
- **New internal interface** `TeamsAttributor` (private to
  `services/vexa-bot/core/`).
- **`handleTeamsAudioData` signature change** — internal helper, only
  called from `platforms/msteams/recording.ts`. Not a public export.
- **New env vars**: none on the happy path. (`DIARIZATION_DEBUG=true`
  optional, dumps per-commit attribution decisions to log.)
- **Speaker naming during caption-lag**: commits with no caption-time
  overlap publish with `speaker_<N>` as the speaker name until
  `updateSpeakerName` retro-fixes the name when captions resolve. The
  dashboard's existing rename handling renders this as a name change.
- **Caption event semantics**: captions still emit
  `started_speaking` speaker events for downstream consumers; only the
  buffer-flush coupling is removed.

## Isolation requirements

- Worktree: `vexa-pack-msteams-diarization-cutover` (separate from the
  predecessor RnD worktree, which lives at
  `vexa-pack-pack-msteams-local-diarization-rnd`).
- Branch: `codex/pack-msteams-diarization-cutover`.
- Compose lane: separate port allocations from the predecessor and
  from main, so the develop skill can A/B against staging if it wants.
- The RnD pack is preserved as ground-truth — this pack does not delete
  `services/vexa-bot/rnd/diarization/`. Removing the RnD harness is an
  explicit deferred decision.

## Compose validation gate

- `make -C deploy/compose up` succeeds with the patched
  `vexa-bot` image.
- Bot container starts, loads both ONNX models from the baked-in
  cache (no Hugging Face network call), logs:
  - `[onnx-diarizer] pyannote ready` (added by predecessor pack)
  - `[onnx-diarizer] wespeaker model ready`
- A `/health` GET on the bot service returns 200 within 30 s of compose
  start.
- A synthetic Teams join is not required at this gate — that's the live
  gate. Compose is "the container boots, the models load, no crash".

## Lite validation gate

- `make -C deploy/lite up` (single-container Vexa Lite deploy) succeeds
  with the same bot image variant. Same model-load + health
  expectations as compose.
- The bot's Lite-mode in-process Whisper/segment pipeline still
  receives audio via the new diarizer path. Smoke test: feed the lite
  bot a 30 s recorded Teams meeting WAV via the existing
  `replay-meeting` test pipeline; verify segments are published with
  speakers attributed.

## Synthetic validation gate

- Existing pack-level eval suites re-run in the new production
  location:
  - `npm run eval:suite` (Piper-rendered 6-corpus suite) — must hold
    `useful=6/6` and BALANCED no worse than 78%.
  - `npm run eval:score` (Whisper-WER + collab attribution across 9
    corpora including the All-In ep 273 ground-truth set) — must
    hold `transcript ≥ 91%`, `purity ≥ 96%`, `recall ≥ 90%`,
    `BALANCED ≥ 79%`.
  - `npm run eval:pyannote` (pyannote-only boundary recall A/B) —
    must hold `recall@500ms ≥ 87%`, `strict@200ms ≥ 87%`.
- New unit tests for `TeamsAttributor`:
  - Caption-correlation match correctness on synthetic caption streams.
  - Session-wide cluster-vote fallback when no caption overlaps a
    commit's time range.
  - Late-caption rename trigger fires `updateSpeakerName` for the
    right commits.
  - Multi-speaker overlap window: dominant-time-coverage wins,
    tie-break is deterministic (longest-suffix).
- Existing bot tests
  (`services/vexa-bot/core/src/services/speaker-streams.*test.ts`)
  continue to pass — the `speakerManager` API surface is unchanged.

## Live / human validation gate

- A real MS Teams meeting smoke test (operator joins a meeting alongside
  the bot, runs the
  `vexa-meeting-deployment-test` skill scenario):
  - The bot joins the meeting.
  - The two ONNX models load from baked-in cache (verify via
    container logs).
  - Transcripts publish to the dashboard with speakers attributed.
  - Manually verify ≥ 5 turn-taking events match audible reality
    (i.e., the right name appears for the right utterance, within
    ±1 s tolerance to account for caption lag).
  - Manually verify zero hallucinations on a 10-minute meeting where
    everyone speaks: no "Amen.", "Thanks for watching", "What?",
    "laughter", etc.
  - Caption-lag check: when a speaker starts before captions catch
    up, the dashboard initially shows `speaker_<N>` and renames to
    the real name within ~3 s.
- Operator-eyeball sign-off explicitly required at the human gate:
  side-by-side comparison of current-production attribution vs new
  attribution on the same recorded meeting (offline replay via
  `replay-meeting`) — operator confirms the new path is "≥ as good" on
  speaker boundaries and "noticeably cleaner" on transcripts.

## PR readiness checklist

- [ ] Worktree created at `vexa-pack-msteams-diarization-cutover`
- [ ] Branch `codex/pack-msteams-diarization-cutover` exists with the
      changes
- [ ] `services/vexa-bot/core/src/services/diarization/` ported from
      RnD pack, types compile (`tsc --noEmit` clean)
- [ ] `services/vexa-bot/core/src/services/teams-attributor.ts`
      implemented with the four unit-test scenarios above passing
- [ ] `handleTeamsAudioData` signature change applied to all callsites
      (currently only `platforms/msteams/recording.ts`)
- [ ] `lastCaptionSpeakerId` flush deletion verified — no remaining
      references in `index.ts`
- [ ] AudioWorklet lowpass ported to the bot's browser-context capture
      code
- [ ] `Dockerfile` updated to bake both ONNX models at build time;
      image builds cleanly
- [ ] Synthetic validation gate passes (eval suite numbers held)
- [ ] Compose validation gate passes (bot starts, health 200)
- [ ] Lite validation gate passes (replay-meeting smoke)
- [ ] Live validation gate passes (real Teams meeting, operator-eyeball
      sign-off)
- [ ] CHANGELOG entry drafted
- [ ] Hardenloop pass: no new secret material in the image, no
      new external services in deploy graphs, ONNX model files have
      content-hash check at load time
- [ ] PR targets the release integration branch (TBD; assigned by
      release skill at stitch time)

## Stitching risk notes

- **Migration order**: this pack must land AFTER the RnD pack PR
  (`#379`) is merged or its branch is otherwise tagged, so the
  production code can reference the validated diarizer settings as a
  known reference point. If the RnD PR is still open at stitch time,
  the release skill should flag this as a sequencing dependency.
- **Image-size growth (+32 MB)** is the largest single regression in
  this pack. The release's hardenloop image-budget gate may need
  explicit acknowledgement. Mitigation: ONNX models are deduplicated
  across bot containers via the base-image layer cache.
- **No fallback path** is explicit user direction. If the diarizer
  fails to initialise (model load error, ONNX runtime crash), the bot
  fails to start with a hard error. This is a behaviour-change vs the
  current bot, which would happily continue with caption-driven
  attribution. The release skill should call this out in the human-
  review pack-by-pack callouts.
- **Cross-pack dependency on `SegmentPublisher` rename mechanism**: the
  `updateSpeakerName` path is currently used by GMeet's track-to-name
  resolution; this pack reuses it for late-caption attribution. If a
  concurrent pack changes that mechanism, conflict resolution is
  required at stitch time. Check for in-flight packs touching
  `speaker-streams.ts` or `index.ts:1571-1700`.
- **Performance regression risk under load**: the eval measures
  single-bot performance. A many-bots-per-host deploy may exceed CPU
  budget. The release's compose-deploy + helm-deploy lanes need to
  validate per-bot CPU stays under 30% of one core under realistic
  load. If it exceeds, model quantisation (q8) is the obvious lever
  but not part of this pack's scope.

## Pack metadata

- Pack id: `pack-msteams-diarization-cutover`
- Release: `TBD — bind to next numbered Vexa release at release-staging time`
- Base branch: `main`
- Integration branch: `main` (or release integration branch — assigned at stitch time)
- Runtime namespace: `pack-msteams-diarization-cutover`
- Evidence root: `.agents/packs/pack-msteams-diarization-cutover/`
- Lifecycle labels at creation: `pack`, `status:available`
