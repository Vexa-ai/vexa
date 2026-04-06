---
needs: [DATASET_PATH]
gives: [WER, SPEAKER_ACCURACY, COMPLETENESS]
---

# RT Replay

> **Why:** Live meetings are expensive. Replay lets you iterate on quality without human involvement — the cheap inner loop.
> **What:** Replay a captured dataset through the pipeline at real-time speed, score each segment against ground truth for WER, speaker accuracy, and completeness.
> **How:** Feed audio into pipeline, compare output segments line-by-line to ground truth, classify errors (substitution, deletion, hallucination, wrong speaker).

## state

    WER              = 0
    SPEAKER_ACCURACY = 0
    COMPLETENESS     = 0
    SEGMENT_COUNT    = 0

## steps

```
1. verify_dataset
   do: ls {DATASET_PATH}/ground-truth.json
   expect: exists
   on_fail: stop

2. replay
   do: DATASET={DATASET_NAME} node delivery/tests/generate-core.js
   expect: produces transcript.jsonl
   on_fail: stop

3. score
   for GT_LINE in ground_truth:
       > Find best matching segment (similarity >= 70%).
       > Check speaker, check content.
       if no match: emit FINDING "MISSED: {GT_LINE.text}"
       if speaker wrong: emit FINDING "SPEAKER: expected {GT_LINE.speaker}, got {actual}"
       if content < 70%: emit FINDING "LOW MATCH: {pct}%"
       on_fail: continue

   for SEGMENT without GT match:
       emit FINDING "HALLUCINATION: {SEGMENT.text}"

4. compute
   => WER
   => SPEAKER_ACCURACY
   => COMPLETENESS
   => SEGMENT_COUNT
   emit FINDING "WER={WER} speaker={SPEAKER_ACCURACY} complete={COMPLETENESS}"

5. evaluate
   expect: WER < 0.15
   expect: SPEAKER_ACCURACY >= 0.95
   expect: COMPLETENESS >= 0.90
   if all pass: emit PASS "quality meets thresholds"
   else: emit FAIL "quality below threshold"
   on_fail: continue
```
