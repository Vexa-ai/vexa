# Whisper Hallucination Pipeline

## WHY -- what hallucination looks like and why it happens

Whisper hallucinates when it receives silence or low-energy audio. Instead of outputting nothing, it generates confident-sounding text -- often repetitive loops ("so much so much so" x37, "i will draft the api" x34) or known stock phrases ("Thank you for watching", "Subscribe to my channel").

Root causes:

1. **Whisper was not trained to output nothing.** Its training data always has speech, so silence is out-of-distribution. The model "fills" silence with high-confidence garbage.
2. **Repetition loops are self-reinforcing.** When `condition_on_previous_text=True`, a hallucination in segment N becomes context for segment N+1, causing cascading loops across the entire remainder of the audio.
3. **Multi-speaker separation creates silence gaps.** Our pipeline separates each speaker into their own track. A speaker who is silent for 30-60 seconds while others talk produces long silence gaps that are prime hallucination triggers.
4. **`no_repeat_ngram_size` only works within a single decode step**, not across segments. So it prevents "the the the" within one segment but not "so much so" repeated across segment boundaries.

## WHAT -- current 3-layer protection and where it fails

### Layer 1: transcription-service (faster-whisper parameters)

| Parameter | Current Value | Status |
|---|---|---|
| `BEAM_SIZE` | 5 | OK |
| `BEST_OF` | 5 | OK |
| `COMPRESSION_RATIO_THRESHOLD` | 1.8 | Aggressive (default 2.4) -- good |
| `LOG_PROB_THRESHOLD` | -1.0 | Default -- could be stricter |
| `NO_SPEECH_THRESHOLD` | 0.6 | **Too high** -- Whisper thinks silence is "speech it can't figure out" |
| `CONDITION_ON_PREVIOUS_TEXT` | False | Correct -- prevents cascading loops |
| `NO_REPEAT_NGRAM_SIZE` | 3 | Good but only works within decode steps |
| `REPETITION_PENALTY` | 1.1 | Moderate -- could go to 1.2 |
| `VAD_FILTER` | True | Good but bypassed in deferred path |
| `VAD_FILTER_THRESHOLD` | 0.5 | OK |

### Layer 2: WhisperLive (phrase lists)

Known hallucination phrases are matched and suppressed. Catches stock phrases like "Thank you for watching" but cannot catch novel repetitive content since the phrases differ each run.

### Layer 3: bot (hallucination-filter.ts)

- Phrase matching (same limitation as Layer 2)
- Too-short segment check
- Repetition detection: 3-6 word n-gram repeated 3+ times
- Compression ratio > 2.0
- no_speech_prob > 0.6

### Where it fails

1. **Deferred/batch transcription bypasses Layer 3 entirely.** The bot filter only runs in the live path. Post-meeting re-transcription sends full recordings without the bot's hallucination-filter.ts.
2. **Deferred transcription may bypass VAD.** Full recordings sent without VAD pre-segmentation means long silence gaps go straight to Whisper.
3. **`no_repeat_ngram_size` does not work across segments.** A 37x repetition that spans multiple segments is invisible to the decode-time n-gram check.
4. **Low-level noise (-15 to -20dB) causes VAD false negatives.** Background pink noise makes Silero VAD think silence is speech, letting silence+noise through to Whisper.
5. **`no_speech_threshold=0.6` is too permissive.** Research consensus is 0.2-0.4 for reliable silence detection.

## HOW -- specific fixes needed

### Fix 1: Add VAD pre-segmentation to deferred transcription path

The single most impactful fix. Before sending audio to Whisper in the deferred/batch path, run Silero VAD to extract speech segments and discard silence.

```
Full recording -> Silero VAD -> Extract speech segments -> Whisper per segment -> Reassemble with timestamps
```

This must happen in **transcription-service** so it covers both live and deferred paths.

### Fix 2: Tune transcription-service parameters

Changes to apply:

| Parameter | Current | New | Rationale |
|---|---|---|---|
| `NO_SPEECH_THRESHOLD` | 0.6 | **0.3** | Research consensus: 0.6 is too permissive; 0.2-0.4 catches more silence |
| `LOG_PROB_THRESHOLD` | -1.0 | **-0.5** | Stricter confidence filtering; discards low-confidence hallucinated segments |
| `REPETITION_PENALTY` | 1.1 | **1.2** | Slightly stronger penalty; avoid going above 1.3 as it degrades real speech |

Keep `COMPRESSION_RATIO_THRESHOLD=1.8` (already more aggressive than the 2.4 default) and `CONDITION_ON_PREVIOUS_TEXT=False` (already correct).

### Fix 3: Add post-processing hallucination filter in transcription-service

This filter runs after Whisper returns segments, before results are sent downstream. It covers both live and deferred paths.

**Multi-signal segment filter:**

```python
def is_hallucination(segment) -> bool:
    # Signal 1: high no_speech probability
    if segment.no_speech_prob > 0.5:
        return True
    # Signal 2: low confidence + moderate no_speech
    if segment.avg_logprob < -0.5 and segment.no_speech_prob > 0.3:
        return True
    # Signal 3: repetitive output (gzip compression ratio)
    text_bytes = segment.text.encode("utf-8")
    compressed = zlib.compress(text_bytes)
    gzip_ratio = len(text_bytes) / len(compressed)
    if gzip_ratio > 2.4:
        return True
    # Signal 4: n-gram repetition (3-gram repeated 4+ times)
    words = segment.text.lower().split()
    ngram_counts = {}
    for i in range(len(words) - 2):
        ngram = tuple(words[i:i+3])
        ngram_counts[ngram] = ngram_counts.get(ngram, 0) + 1
        if ngram_counts[ngram] > 4:
            return True
    return False
```

**Why in transcription-service:** This is the only place that sits on both the live and deferred code paths. The bot filter only covers live.

### Fix 4: Handle VAD noise bypass

For low-level background noise causing VAD false negatives:

- Increase VAD threshold from 0.5 to **0.6-0.7** for the deferred path (can be more aggressive since latency does not matter)
- Optionally add a noise gate or high-pass filter before VAD to strip low-frequency pink noise

### Fix 5: Consider `hallucination_silence_threshold` (faster-whisper)

faster-whisper supports `hallucination_silence_threshold` which skips silence periods when hallucination is detected. Requires `word_timestamps=True`. Recommended value: **2-8 seconds**.

Caveat: this parameter is **ignored when using batched inference with VAD filter**. These two features are mutually exclusive in faster-whisper.

---

### Priority order

1. **Fix 1** (VAD in deferred path) -- closes the biggest gap, prevents silence from reaching Whisper
2. **Fix 2** (parameter tuning) -- low-effort, high-impact, just env var changes
3. **Fix 3** (post-processing filter) -- catches anything that gets through VAD + parameter gates
4. **Fix 4** (noise handling) -- edge case but important for noisy environments
5. **Fix 5** (hallucination_silence_threshold) -- additional safety net if not using batched inference

### References

- [arxiv 2501.11378: Investigation of Whisper ASR Hallucinations](https://arxiv.org/abs/2501.11378) -- BoH + delooping approach
- [arxiv 2505.12969: Calm-Whisper](https://arxiv.org/abs/2505.12969) -- 3 attention heads cause 75%+ of hallucinations
- [Whisper PR #1838: hallucination_silence_threshold](https://github.com/openai/whisper/pull/1838)
- [Whisper Discussion #679: Hallucination solutions](https://github.com/openai/whisper/discussions/679)
- [faster-whisper #478: repetition_penalty usage](https://github.com/SYSTRAN/faster-whisper/issues/478)
