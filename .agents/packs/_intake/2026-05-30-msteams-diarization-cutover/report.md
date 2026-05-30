# Pack intake report — 2026-05-30-msteams-diarization-cutover

## Method

This is a **design-driven pack proposal**, not an issue-mining run. The
operator directed an explicit cutover scope based on validated RnD work
in the predecessor pack `pack-msteams-local-diarization-rnd` (issue #378,
PR #379, branch `codex/pack-pack-msteams-local-diarization-rnd`).

No raw GitHub issues were collected. `issues.json` documents the
predecessor pack artefacts; `pack-proposals.json` contains a single
proposal.

## Proposed packs

| Pack id | Title | Status |
|---|---|---|
| `pack-msteams-diarization-cutover` | MS Teams diarization cutover — replace caption-driven audio gating with pyannote segmentation + blue-square correlation | proposed (dry-run) |

## Out-of-scope / needs-triage

None at this run. The proposal already enumerates explicit
out-of-scope items inside the pack epic body (GMeet/Zoom platform
ports, same-gender corpus regression, RnD sandbox removal, wespeaker
replacement, speaker rename UI changes).

## Grouping rationale

The pack groups exactly one coherent outcome: replace MS Teams
caption-driven audio gating with the validated diarizer pipeline. One
business outcome (correctly attributed Teams transcripts), one
engineering invariant (audio-driven boundaries + caption-driven
identity, no caption-flush coupling), one user-visible promise
(transcripts switch speakers at the audible change moment).

The pack does NOT mix in:

- Google Meet or Zoom port work (different audio pipelines, separate
  business decision).
- Same-gender corpus regression fix (architectural improvement on top
  of pyannote — not a blocker for the cutover).
- Speaker rename UI (independent UX surface).

## Validation surface

All four validation gates have specific exit criteria:

- **Compose** — bot container starts, both ONNX models load from
  baked-in cache, no Hugging Face network call.
- **Lite** — same as compose, plus replay-meeting smoke test.
- **Synthetic** — three eval suites must hold their predecessor-validated
  numbers (`useful=6/6` on Piper suite, `BALANCED ≥ 79%` on
  transcript-score, `recall@500ms ≥ 87%` on pyannote-only A/B), plus
  new unit tests for `TeamsAttributor`.
- **Live/human** — real Teams meeting smoke + operator-eyeball
  side-by-side comparison vs current production.

## Stitching dependencies

Two explicit dependencies the release skill should track:

1. RnD pack PR #379 must be merged/tagged before this pack lands so the
   production code can reference the validated diarizer settings as a
   known reference point.
2. `SegmentPublisher.updateSpeakerName` is reused here for late-caption
   attribution; check for in-flight concurrent packs touching
   `speaker-streams.ts` or `index.ts:1571-1700` at stitch time.

## Hard stops respected

- No branch / worktree / runtime lane created.
- No code changes made.
- No GitHub mutation. The proposal is dry-run only. The
  `upsert-pack-epics.sh` script was NOT invoked.
- No transition to `status:in-progress`. The proposal labels are
  `pack` + `status:available` if and when an operator applies it.

## Next operator actions

1. Review `bodies/pack-msteams-diarization-cutover.md` for content
   accuracy.
2. If approved for GitHub creation, run:
   ```bash
   .claude/skills/pack/scripts/upsert-pack-epics.sh \
     --proposal .agents/packs/_intake/2026-05-30-msteams-diarization-cutover/pack-proposals.json \
     --out-dir .agents/packs/_intake/2026-05-30-msteams-diarization-cutover/ \
     --apply
   ```
3. After GitHub issue creation, the `develop` skill claims the
   `status:available` pack and begins implementation in an isolated
   worktree.
