# Code review verdict — pack-msteams-diarization-cutover (#394)

**Status:** PENDING HUMAN REVIEWER

The develop skill prepared the diff and the machine-generated
`review.md` skeleton. A human must read the actual code diff and
record an explicit verdict below.

## Verdict (to be filled by a human)

```
Verdict: [ pass | pass with notes | changes requested | block ]
Reviewer: <name / email>
Timestamp: <ISO-8601>

Diff read at: <sha = 838ffd9>  (or whatever HEAD is at review time)

Blast-radius surfaces reviewed:
  [ ] (1) MS Teams audio capture path (not modified — Phase C note)
  [ ] (2) MS Teams speaker attribution path (caption → diarizer)
  [ ] (3) Transcript publication
  [ ] (4) Late-rename for provisional clusters
  [ ] (5) Pipeline teardown
  [ ] (6) Docker image cold-start + size

Scope discipline:
  [ ] Diff stays within services/vexa-bot/core/, services/vexa-bot/Dockerfile,
      deploy/lite/Dockerfile.lite, and .agents/packs/<pack-id>/.
  [ ] No unrelated refactors.
  [ ] No hidden stitch-time changes.
  [ ] No tests3/ modifications.

Notes:
  - <reviewer observations>
```

## Where to find the material

- Pack epic: GitHub issue #394.
- Branch: `codex/pack-pack-msteams-diarization-cutover`.
- Machine review skeleton: `.agents/packs/<pack-id>/review.md`.
- Synthetic gate: `.agents/packs/<pack-id>/synthetic/synthetic-gate.md`.
- Compose evidence (partial): `.agents/packs/<pack-id>/compose/`.
- Lite evidence (partial): `.agents/packs/<pack-id>/lite/`.
- Ops ledger: `.agents/packs/<pack-id>/ops/ops.jsonl`.

## Reviewer guidance

Pay particular attention to:

1. **No-fallback hardness.** `services/vexa-bot/core/src/index.ts`
   `initPerSpeakerPipeline`'s Teams branch throws on diarizer-load
   failure. Confirm there is **no remaining caption-driven flush
   anywhere** in `handleTeamsCaptionData` or `handleTeamsAudioData`.

2. **Cluster-id contract.** `speakerManager.addSpeaker(speakerId,
   speakerName)` is now called with `speakerId = ev.speakerId` (the
   diarizer's cluster_id) instead of a caption-derived id. Any
   downstream code keyed on the speaker map (segment publisher,
   transcript renderer) should still work — verify.

3. **Hallucination gate constants.** RMS ≥0.012, ≥600ms min-speech,
   ≥50% speech-ratio. Pulled directly from the RnD harness's
   `server.ts` — they're duplicated here without a shared constants
   module. Acceptable for the cutover; flag if you'd rather centralise.

4. **`@huggingface/transformers` v4.2.0** is a new bot dep. ~150 MB
   in node_modules. Review the license + supply-chain implication.

5. **Docker bake step.** Adds ~32 MB to the runtime image. Verify
   that the cache path
   (`node_modules/@huggingface/transformers/.cache/...`)
   propagates to the runtime stage and that transformers.js reads
   from it at runtime (`env.allowLocalModels = true`).

(Verdict to be filled by an operator/reviewer; do NOT pre-fill.)
