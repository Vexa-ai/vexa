# Review skeleton — pack-msteams-diarization-cutover (#394)

This file is the **machine-generated** review skeleton. The
develop skill never writes its own verdict here. A human reviewer must
read the diff, fill in `code-review.md` with the explicit verdict
(`pass` / `pass with notes` / `changes requested` / `block`), and
record reviewer identity + timestamp.

## Scope

**Outcome:** replace MS Teams caption-driven audio-channel activation
with pyannote/segmentation-3.0-driven boundaries + DOM-blue-box
correlation for speaker attribution. **No fallback** — if the diarizer
fails to load the bot session errors out.

## Commits on the branch (`codex/pack-pack-msteams-diarization-cutover`)

| sha | subject |
|---|---|
| 1571ae7 | feat: port diarization sources to production location |
| 347c035 | chore: evidence — claim, pack parse, runtime allocation, preflight, worktree manifest, ops ledger |
| c4ed147 | feat: TeamsAttributor — caption-correlation + cluster-vote for diarizer commits |
| 72eb53a | feat: rewrite handleTeamsAudioData + delete caption-driven flush |
| a2feba2 | feat: bake diarization ONNX models into the runtime image |
| 838ffd9 | evidence: synthetic eval gate — recall 90.1%, purity 95.7%, pyannote A/B beats baseline |

`git diff --stat main..HEAD`: 26 files, 4188 insertions, 31 deletions.

## Diff inventory

### New files (algorithmic)

- `services/vexa-bot/core/src/services/diarization/onnx-local-diarizer.ts` (1028 lines)
- `services/vexa-bot/core/src/services/diarization/pyannote-segmenter.ts` (259 lines)
- `services/vexa-bot/core/src/services/diarization/online-clustering.ts` (292 lines)
- `services/vexa-bot/core/src/services/diarization/diarizer.ts` (43 lines)
- `services/vexa-bot/core/src/services/diarization/metrics.ts` (388 lines)
  → ported from `services/vexa-bot/rnd/diarization/src/`. 4 of 5 are
    byte-identical; `onnx-local-diarizer.ts` drops 4 lines of unused
    `__filename`/`__dirname` reconstruction (CJS adaptation).

- `services/vexa-bot/core/src/services/teams-attributor.ts` (293 lines)
  → new code. Caption-correlation algorithm with three resolution paths:
    (a) window-match within `matchToleranceMs` of commit window
        (lag-shifted by `captionLagMs`);
    (b) cluster-vote: majority of past captions associated with the
        same cluster_id;
    (c) provisional: return `cluster_id` as the placeholder name; emit
        `onLateResolve` callback once enough caption evidence accrues.

- `services/vexa-bot/core/src/services/teams-attributor.test.ts` (134 lines)
  → 22 assertions, all pass under `npx tsx` runner.

- `services/vexa-bot/core/scripts/bake-diarization-models.js` (51 lines)
  → Docker-build helper. Downloads two ONNX models into the
    transformers.js cache (`node_modules/@huggingface/transformers/.cache/`).

### Modified files

- `services/vexa-bot/core/src/index.ts` (+180/-25)
  - module-level singletons: `teamsDiarizer`, `teamsAttributor`, `teamsPendingFrames`.
  - `initPerSpeakerPipeline`: new Teams branch — instantiates attributor +
    diarizer; diarizer's `onCommit` resolves speaker via attributor,
    manages add/rename, drains pending frames with hallucination gate
    (RMS ≥ 0.012, ≥600ms speech, ≥50% speech-ratio); **throws** on
    diarizer-load failure (no fallback).
  - `handleTeamsAudioData`: rewritten. `speakerName` is advisory
    (recorded into attributor); audio is buffered into
    `teamsPendingFrames` and pushed to the diarizer. **Caption no longer
    triggers a flush.**
  - `handleTeamsCaptionData`: deleted the
    `if (lastCaptionSpeakerId && lastCaptionSpeakerId !== speakerId) flushSpeaker(...)`
    block. Captions only feed the attributor now.
  - `cleanupPerSpeakerPipeline`: tears down diarizer + attributor and
    empties the pending-frames buffer.

- `services/vexa-bot/core/package.json` (+1)
  - `"@huggingface/transformers": "^4.2.0"` (Node.js ONNX runtime).

- `services/vexa-bot/core/tsconfig.json` (+1)
  - `"skipLibCheck": true` (worked around @huggingface/tokenizers
    internal alias paths `@utils`, `@static/tokenizer` that tsc can't
    resolve).

- `services/vexa-bot/Dockerfile` (+8)
  - ts-builder stage: `COPY core/scripts/ ./scripts/` then
    `RUN node scripts/bake-diarization-models.js` after `npm run build`.
  - Cache (~32 MB) flows to runtime via existing
    `COPY --from=ts-builder /app/vexa-bot/core/node_modules` line.

- `services/vexa-bot/package-lock.json` (+696)
  - Lockfile entries for `@huggingface/transformers` and transitive
    dependencies. Auto-generated.

## Blast-radius surfaces (from pack epic)

The pack epic declares the following surfaces in scope. Each requires
human verification in **both** Compose and Lite lanes (`compose/` and
`lite/` evidence dirs).

1. **MS Teams meeting join + audio capture (browser context).** The
   bot's `recording.ts` still uses
   `new AudioContext({ sampleRate: 16000 })` → built-in polyphase
   resample. **Not modified by this pack.** Phase C note records the
   decision.
2. **MS Teams speaker attribution path** (caption-driven flush →
   diarizer-driven commit + attributor resolution). **Wholly replaced.**
3. **Transcript publication (segment publisher).** The diarizer's
   resolved speaker name feeds `speakerManager.feedAudio`; the rest of
   the publisher pipeline is unchanged.
4. **Late-rename for provisional clusters.** `onLateResolve` calls
   `speakerManager.updateSpeakerName` when cluster-vote eventually
   resolves a placeholder.
5. **Pipeline teardown.** `cleanupPerSpeakerPipeline` resets diarizer
   + attributor, empties pending frames.
6. **Docker image size + cold-start network.** +32 MB for cached ONNX
   models; first-meeting model-load latency now zero (was a 1.5s HF
   download).

## Scope discipline

The diff stays inside `services/vexa-bot/core/`, `services/vexa-bot/Dockerfile`,
and `.agents/packs/pack-msteams-diarization-cutover/`. **No** unrelated
refactors. **No** `tests3/` modifications.

## Synthetic gate (Phase E)

✅ PASS. See `.agents/packs/pack-msteams-diarization-cutover/synthetic/synthetic-gate.md`.

| metric | observed | gate |
|---|---|---|
| boundary recall @500ms | 90.1% (suite) / 90.4% (score) / 87.9% (pyannote-probe) | ≥87% |
| segment purity | 95.7% / 96.2% | ≥90% |
| collab acc (realistic noise) | 96.9% | ≥90% |
| pyannote strict@200ms vs wespeaker baseline | 87.9% vs 85.3% | beat baseline |

## Open gates pending operator action

These cannot be satisfied without human signal:

| gate | evidence file | who |
|---|---|---|
| Compose lane up + blast-radius checks | `compose/meeting-deployment-test.md` | operator (transcription-token re-issue + `make all-build`) |
| Lite lane up + blast-radius checks | `lite/meeting-deployment-test.md` | operator |
| Human eyeball — Compose basic | `compose/human-eyeball-basic.md` | operator |
| Human eyeball — Compose blast-radius | `compose/human-eyeball-blast-radius.md` | operator |
| Human eyeball — Lite basic | `lite/human-eyeball-basic.md` | operator |
| Human eyeball — Lite blast-radius | `lite/human-eyeball-blast-radius.md` | operator |
| Live MS Teams meeting test (Compose) | `compose/meeting-deployment-test.md` | operator + approved Teams URL |
| Live MS Teams meeting test (Lite) | `lite/meeting-deployment-test.md` | operator + approved Teams URL |
| Human overall functionality verdict | `human/overall-functionality.md` | operator |
| Code review verdict | `code-review.md` | human reviewer |
| Hardenloop | `hardenloop/` | develop skill once human gates clear |

## Notes for the reviewer

1. **No-fallback is explicit and intentional.** `initPerSpeakerPipeline`
   throws if the diarizer fails to load (model fetch from cache or
   ONNX-runtime instantiation). Pre-baking the models in the Docker
   image makes this safe in production; on a dev workstation a clean
   `node_modules` will need network access on first cold-start to
   populate the transformers.js cache.

2. **Caption is now advisory.** Anything reading the caption-driven
   `speakerName` parameter of `handleTeamsAudioData` will be quietly
   ignored. Search for downstream consumers if the bot has tests
   asserting on caption-derived speakers.

3. **Pending-frames cap** is 1500 frames (~30s at 50Hz frame rate).
   The diarizer commits every ~500ms in steady state, so this is
   a defensive bound, not an expected hot path.

4. **Hallucination gates** (RMS ≥0.012, ≥600ms min-speech, ≥50%
   speech-ratio) are duplicated between the diarizer's `onCommit`
   handler and the RnD harness's `server.ts`. If we tune them, both
   sites need to move together — consider a shared constants file
   in a follow-up.
