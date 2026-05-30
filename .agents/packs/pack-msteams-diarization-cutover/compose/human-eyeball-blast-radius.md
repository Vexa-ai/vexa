# Compose lane — human eyeball verdict: BLAST RADIUS

**Pack:** pack-msteams-diarization-cutover (#394)
**Lane:** Compose (`pack-msteams-diar-cutover-compose`, ports 42300-42314)
**Status:** PENDING OPERATOR

## What this verdict covers

The **specific surfaces this pack touches** in the Compose lane. From
the pack epic's blast-radius declaration:

1. MS Teams audio capture path.
2. MS Teams speaker attribution path (caption-driven → diarizer-driven).
3. Transcript publication with diarized speaker names.
4. Late-rename for provisional cluster_id speakers.
5. Pipeline teardown (no leaked state across sessions).
6. Docker image cold-start time + size.

## Checklist for the operator

| surface | check | expected | observed | OK? |
|---|---|---|---|---|
| (1) Audio capture | bot logs show `[MS Teams][Audio]` lines streaming | continuous PCM frames | _( )_ | |
| (2a) Diarizer initialised | bot logs `[Teams diarizer] OnnxLocalDiarizer (pyannote/segmentation-3.0 + wespeaker) ready` | line present at session start | _( )_ | |
| (2b) Caption is advisory | bot logs **NO** `[MS Teams][Audio] FLUSH speaker [...] != [...]` lines | absent (legacy flow disabled) | _( )_ | |
| (2c) Diarizer commits | bot logs `[onnx-diarizer] commit utterance dur=<N>s → speaker_<k>` | multiple per minute | _( )_ | |
| (2d) Attribution path | bot logs `[Teams diarizer] late-resolve: cluster speaker_X → "<name>"` | fires when caption majority emerges | _( )_ | |
| (3) Transcript on dashboard | dashboard shows speakers with caption-correlated names, not raw cluster ids | named speakers visible | _( )_ | |
| (4) Late-rename | speakers initially shown as `speaker_<id>`, swapped to display name once vote crosses threshold | observable in real time | _( )_ | |
| (5) Teardown | next bot session has independent cluster ids (no leak) | fresh `speaker_0` numbering | _( )_ | |
| (6a) Image size | `docker images vexaai/vexa-bot:<tag>` | ~4.4 GB (+32 MB vs main) | _( )_ | |
| (6b) Cold-start has no HF fetch | bot logs **NO** "downloading from huggingface.co" lines | models load from cache | _( )_ | |
| No fallback | if you `docker rm` the ONNX cache and restart, bot session **fails fast** | exception thrown, bot exits non-zero | _( )_ | |

## Hallucination check (negative test)

Watch for known false-positive phrases in the transcript:
- "laughter", "music", "(applause)", "..."
- single-word utterances during silence

Expected: **none**, due to the 3-tier hallucination gate
(RMS ≥0.012, ≥600ms speech, ≥50% speech-ratio).

## Verdict template

```
Verdict: [ pass | pass with notes | changes requested | block ]
Reviewer: <name / email>
Timestamp: <ISO-8601>
Notes: <any divergence from expected; especially any caption-driven
       behaviour observed, which would mean the pack regressed the
       legacy flow deletion>
```

(Verdict to be filled by an operator.)
