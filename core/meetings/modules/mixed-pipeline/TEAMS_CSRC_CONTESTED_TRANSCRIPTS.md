# Teams CSRC contested words

Status: **explicitly unresolved** (2026-08-14). Microsoft Teams supplies one decoded mixed PCM
stream, not pre-mix PCM per CSRC. The candidate therefore detects duplicated words but does not use
transport, transcript context, Whisper confidence, row length, or timing coverage to guess a human.

## Current contract

A phrase is detected as contested only when rows from different CSRCs satisfy all three observations:

1. their routed mixed-audio intervals overlap;
2. their normalized transcript contains the same contiguous phrase above the configured text
   threshold; and
3. consensus Whisper word times for that phrase are close enough across the two lane windows.

This is duplicate detection, not speaker identification. Every detected pair stays in the
transcript under both CSRCs with verbatim confirmed text. The exact shared phrase may be wrapped in
evaluation-only diagnostics:

```text
CSRC 201: it. ⟦It's not like plug and⟧{CSRC 201↔CSRC 840} plug.
CSRC 840: ⟦It's not like plug and⟧{CSRC 840↔CSRC 201} play kind of PowerPoint content.
```

There is no winner, loser, score, confidence, deletion, or reassignment. Different simultaneous
wording remains ordinary independent speech. If word-time evidence is missing or too distant, the
detector records no contested pair rather than widening the detected interval.

The production detector is post-confirm inside the Teams pipeline and exposes only the unresolved
pair count through pipeline health. Public transcript text is never rewritten. The browser
evaluation copy stays independent and may render the explicit notation so a fixture can audit the
detector rather than create production output:

```text
/Users/dmitriygrankin/dev/vexa/core/meetings/modules/mixed-pipeline/src/teams-contested-word-marker.ts
/Users/dmitriygrankin/dev/vexa/core/meetings/modules/mixed-pipeline/src/teams-csrc-gmeet-pipeline.ts
/Users/dmitriygrankin/dev/vexa/core/meetings/modules/mixed-pipeline/eval-ui/teams-contested-word-detector.mjs
/Users/dmitriygrankin/dev/vexa/core/meetings/modules/mixed-pipeline/eval-ui/teams-csrc-timeline.mjs
/Users/dmitriygrankin/dev/vexa/core/meetings/modules/mixed-pipeline/eval-ui/teams-csrc-live-model.mjs
```

The marker is an evaluation diagnostic, not a transcript wire format. The API, Dashboard and
exports receive the original confirmed text. A consumer must not infer, widen, resolve, or delete a
contest from text.

## Why the heuristic resolver is rejected

Both CSRC lanes may receive bit-identical mixed PCM. Transport continuity, local transcript context,
Whisper log probability, number of confirming passes, candidate row length, and raw-CSRC coverage
can all favor the wrong speaker. Those features may explain a duplicate, but they cannot establish
which voice produced it. They are not on the candidate publication or evaluation path.

The current path is deliberately:

```text
mixed PCM + CSRC activity
        -> per-CSRC continuous GMeet-compatible windows
        -> confirmed rows
        -> overlap + same phrase + word-time proximity
        -> count the unresolved pair in telemetry
        -> publish both verbatim rows without guessing ownership
```

## Future fix: session-local diarization centroids

The future resolver is acoustic and meeting-local. It runs after duplicate-word detection and before
Teams publication; it does not change the shared GMeet-compatible buffer.

### 1. Build clean reference centroids

For each mapped CSRC, admit a reference audio window only when:

- exactly one CSRC is transport-active for the entire interior window;
- the audio excludes onset lookback, backfill, boundary flicker, and every contested phrase;
- a speech detector finds sufficient voiced duration and no overlapping speaker change;
- the window is long enough for the pinned speaker-embedding model;
- at least three non-adjacent windows from distinct turns agree; and
- leave-one-window-out similarity stays within a bounded dispersion.

Store a robust centroid plus sample count, voiced duration, dispersion, and source-window receipts.
An embedding-selected window can never feed back into the reference bank. Centroids are
meeting-local, memory-only, and destroyed at meeting teardown; cross-meeting voice recognition is a
separate privacy/product decision.

### 2. Isolate the contested acoustic envelope

Use the lexical detector's consensus phrase start/end only to cut a small waveform envelope with
padding. Run speaker-change/overlap segmentation inside that raw mixed-audio cut. The cut is one
physical waveform shared by the rival lanes—not one independent audio sample per CSRC.

If the envelope contains one stable voice region, embed that region and compare it with the eligible
session centroids. If it contains simultaneous voices that cannot be separated, keep both verbatim
rows unresolved. Whisper word times locate the experiment window; they never identify the voice.

### 3. Resolve or abstain

Assignment requires all of:

- sufficient voiced duration and audio quality;
- a stable reference centroid with enough independent windows;
- an absolute similarity floor;
- a calibrated best-versus-runner-up margin that exceeds reference dispersion; and
- no overlap/change ambiguity in the contested fragment.

Failure of any gate returns `assignedCsrc: null` and preserves both wrapped strings. Raw cosine
similarity is evidence, not user-facing confidence.

```ts
interface TeamsContestedAcousticResolution {
  segmentIds: [string, string];
  contestedText: string;
  audioStartMs: number;
  audioEndMs: number;
  candidateCsrcs: number[];
  assignedCsrc: number | null;
  evidence: {
    kind: 'session-local-speaker-embedding-v1';
    model: string;
    referenceWindows: Record<number, number>;
    referenceVoicedMs: Record<number, number>;
    centroidDispersion: Record<number, number>;
    bestScore: number | null;
    runnerUpScore: number | null;
    margin: number | null;
    abstentionReason?: 'no-reference' | 'too-short' | 'speaker-change' | 'overlap' |
      'unstable-reference' | 'low-score' | 'low-margin';
  };
}
```

### Required evaluation

- clean uncontested windows correctly recover their held-out CSRC centroid;
- swapping centroid labels swaps the acoustic assignment, proving text is not choosing the speaker;
- short backchannels, similar voices, weak margins, and missing references abstain;
- simultaneous speech abstains unless the pinned segmenter produces independently validated regions;
- centroid contamination is rejected by dispersion before it can affect publication;
- diverse calls and languages are evaluated by listening to every assignment and every anomaly, not
  by aggregate accuracy alone; and
- meeting teardown proves no centroid remains addressable.

Until those gates pass, contested words remain duplicated with verbatim text and an internal
unresolved-pair signal. That is the only safe current behavior.
