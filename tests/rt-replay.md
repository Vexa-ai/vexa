---
id: test/rt-replay
type: validation
requires: [test/rt-collection]
produces: [WER, SPEAKER_ACCURACY, COMPLETENESS, SEGMENT_COUNT]
mode: machine
---

# RT Replay — Offline Ground Truth Scoring

> Follows [RULES.md](RULES.md). This procedure owns its scripts.

Replay a captured dataset through the real pipeline at real-time speed. Score output against ground truth. This is the cheap inner loop for quality iteration.

## DoD items this test owns

| Feature | # | Check |
|---------|---|-------|
| gmeet | 2 | Each GT line: correct speaker |
| gmeet | 3 | Each GT line: content matches |
| gmeet | 4 | No hallucinated segments |
| gmeet | 5 | No missed GT lines |
| msteams | 2 | Each GT line: correct speaker |
| msteams | 3 | Each GT line: content matches |
| msteams | 4 | No missed GT lines under stress |
| msteams | 5 | No hallucinated segments |
| msteams | 6 | Speaker transitions: no content lost |
| msteams | 8 | Overlapping speech: both speakers captured |
| zoom | 2 | Each GT line: correct speaker despite SFU |
| zoom | 3 | Each GT line: content matches |
| zoom | 4 | No missed GT lines |

## Docs this test owns

This test validates the accuracy claims in:
- [features/realtime-transcription/README.md](../features/realtime-transcription/README.md) — WER numbers, speaker accuracy percentages
- [features/realtime-transcription/gmeet/README.md](../features/realtime-transcription/gmeet/README.md) — voting/locking accuracy claims
- [features/realtime-transcription/msteams/README.md](../features/realtime-transcription/msteams/README.md) — caption attribution accuracy, stress test results

If claimed accuracy doesn't match measured accuracy, update the feature README and log FIX.

## Inputs

| Name | From | Description |
|------|------|-------------|
| DATASET_PATH | rt-collection or existing dataset | Path to tests/testdata/{dataset}/ |

## Steps

```
1  verify dataset exists:
     ls $DATASET_PATH/ground-truth.json → must exist
     ls $DATASET_PATH/audio/ → must have WAV files

2  replay at real-time speed:
     DATASET=$DATASET_NAME node delivery/tests/generate-core.js
     → data/core/$DATASET_NAME/transcript.jsonl
     NOTE: runs at real-time speed intentionally (90s audio = 90s replay)

3  line-by-line ground truth comparison:
     for each GT line (speaker, text):
       find best matching output segment (text similarity ≥ 70%)
       check: speaker correct? → SPEAKER_PASS/FAIL
       check: content matches? → CONTENT_PASS/FAIL (similarity score)
       check: not matched? → MISSED
     for each output segment without GT match:
       → HALLUCINATION

4  report per line:
     GT[1] Alice "quarterly revenue..."  → seg_003 Alice "quarterly revenue..."  SPEAKER:✓ CONTENT:94%
     GT[2] Bob "hire three engineers..."  → seg_007 Bob "higher three engineers..." SPEAKER:✓ CONTENT:87%
     GT[3] Charlie "satisfaction score..." → (no match)                            MISSED
     seg_012 Alice "um yeah"              → (no GT match)                          HALLUCINATION

5  log failures:
     any SPEAKER:✗ → log_finding "line N: expected {GT speaker}, got {actual}"
     any CONTENT < 70% → log_finding "line N: similarity {pct}% below 70%"
     any MISSED → log_finding "line N: GT not found in output"
     any HALLUCINATION → log_finding "segment {id}: no GT match"
```

## Pass criteria

Per GT line:
- Speaker correct (exact match)
- Content matches (≥ 70% text similarity)
- All GT lines found in output (no misses)
- No output segments without GT match (no hallucinations)
- Speaker transitions: all words present (in correct or adjacent speaker segment)

## Outputs

| Name | Description |
|------|-------------|
| WER | Word error rate (0-1) |
| SPEAKER_ACCURACY | Speaker attribution accuracy (0-1) |
| COMPLETENESS | Ground truth coverage (0-1) |
| SEGMENT_COUNT | Total confirmed segments |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
