# Alloy STT pause-bounded code-switch design

**Date:** 2026-07-28
**Status:** Approved for the standalone local Vexa R&D pilot
**Depends on:** `docs/superpowers/specs/2026-07-28-alloy-rnd-merge-readiness-design.md`
**Baseline:** `dbe38e243cfb834bda2ba2fb23bd48f7debfd399`

## Problem

`ALLOY_STT_LANGUAGE_MODE=auto` currently omits the request `language`, but the installed
`faster-whisper-server` still chooses one detected language for an entire uploaded audio window.
The real EN → RU → EN acceptance file therefore returned only its Russian middle leg even though
the English and Russian source controls each passed separately.

The defect is introduced at the STT request boundary: one multilingual window becomes one
single-language decode. The capture, speaker, confirmation, API, and Terminal consumers must not
compensate for that producer behavior.

## Scope

In scope:

- the existing explicit `ALLOY_STT_LANGUAGE_MODE=auto` path;
- automatic language re-detection at natural pause boundaries within one PCM window;
- sequential subrequests to the configured OpenAI-compatible transcription endpoint;
- merged text, segments, words, offsets, duration, and honest aggregate language metadata;
- focused unit, adapter, and real multilingual evidence;
- documentation of the enabled and rollback behavior.

Out of scope:

- changing the upstream-compatible `configured`/unset path;
- language pinning, language buttons, or per-meeting language selection;
- replacing or forking `faster-whisper-server`;
- speaker attribution, capture topology, transcript schema changes, or UI changes;
- arbitrary mid-word/mid-phoneme language changes with no acoustic pause;
- unrelated refactors or production changes outside the Vexa pilot.

## Evidence already established

- A clean integrated Lite image and its runtime front doors passed.
- Natural English and natural Russian requests passed without `language`.
- The natural Microsoft Zira → Irina → Zira request failed because the backend made one
  `WhisperModel.transcribe(..., language=None)` call and kept one detected language for the file.
- Google Meet was correctly not attempted because multilingual acceptance was still red.

## Options

### A. Pause-bounded subrequests in the shared STT adapter — selected

When the existing Alloy auto mode is enabled, find sufficiently long low-energy pauses, split at
their midpoints, submit the chunks sequentially with no `language`, then merge the verbose results
back into one `TranscriptionResult`.

Benefits:

- fixes the defect at the producer boundary shared by both meeting lanes;
- keeps speaker, API, and Terminal contracts unchanged;
- reuses the existing HTTP, retry, filtering, and concurrency behavior;
- has an exact rollback: `ALLOY_STT_LANGUAGE_MODE=configured` or unset;
- can be proved first against the already failing real acceptance shape.

Costs:

- adds sequential inference calls for windows containing qualifying pauses;
- language switches without a pause remain bounded by the backend's single-decode behavior;
- pause detection needs narrow deterministic tests and real-audio validation.

### B. Fork or replace the transcription server

Teach the server to re-detect language per VAD segment or use a backend that already does so.

This could cover more backend-specific cases, but it expands the pilot into Python image ownership,
model-runtime compatibility, packaging, healthcheck, license, and deployment work. It also couples
the product behavior to one server implementation. It is deferred unless option A's single
hypothesis is falsified.

### C. Accept window-level detection and rely on shorter meeting windows

This preserves current code but does not satisfy the exact EN → RU → EN acceptance row and can
silently omit valid language legs. Rejected.

## Selected design

### One owner

Add a small Alloy-marked pause segmenter inside `@vexa/transcribe-whisper`. It owns only:

1. classifying fixed-size PCM analysis frames as speech or low energy;
2. locating pause runs long enough to be safe split points;
3. returning contiguous sample ranges whose union is the original window.

`TranscriptionClient` remains the one owner of WAV encoding, HTTP requests, retries,
low-confidence filtering, and result merging. The bot composition root only enables the behavior
when `ALLOY_STT_LANGUAGE_MODE=auto`; callers and downstream consumers stay unaware of subrequests.

This is proportionate DRY/SOLID: segmentation exists once and is reused by both lanes, configuration
stays at the composition boundary, the client depends on a narrow range contract, and rollback and
tests remain independent. It avoids duplicated lane forks, lowers coupling, and makes later changes
safer to test and maintain without speculative abstraction.

### Pause rule

The initial pilot uses deterministic PCM analysis:

- 20 ms RMS analysis frames;
- an adaptive low-energy threshold derived from the window's speech energy, with a conservative
  absolute floor;
- a qualifying pause of at least 350 ms;
- a split at the pause midpoint;
- no split that would leave less than 600 ms on either adjacent chunk;
- all-silence, uninterrupted, and short windows remain one range.

The segmenter never discards samples and never overlaps ranges. These values are internal pilot
constants, not new environment switches or public tuning contracts.

### Logical request and limiter semantics

One caller invocation remains one logical STT request:

- the existing request-slot limiter is acquired once around the entire segmented operation;
- the optional execution observer reports one waiting/started/finished lifecycle;
- child requests run sequentially inside that slot;
- each child retains the existing per-request retry and timeout behavior;
- any child failure fails the logical call with the existing typed fault rather than returning a
  silently partial transcript.

This preserves telemetry meaning and prevents segmentation from bypassing CPU backpressure.

### Wire behavior

With auto segmentation enabled:

- no child multipart request contains `language`;
- each child uses the existing model, response format, prompt, VAD parameters, authentication,
  timeout, retry, and confidence filter;
- the first child receives the caller's prompt;
- later children receive no inherited prompt so prior-language text cannot pin a new language.

With auto segmentation disabled:

- the client sends exactly one request using the original PCM, configured language, prompt, and
  observer behavior.

### Merge contract

Child responses are merged in audio order:

- `text` is the non-empty trimmed child texts joined by one space;
- segment and word `start`/`end` values are shifted by the child range's sample offset;
- segment order is stable and no offset is changed twice;
- `duration` is the original PCM length divided by the configured sample rate;
- if all non-unknown child language values agree, that language is returned;
- if two or more distinct detected languages occur, aggregate `language` is `mul` (ISO 639-2
  multiple languages);
- if no child reports a language, aggregate `language` is `unknown`;
- aggregate `language_probability` is omitted for `mul`; otherwise it is the minimum reported
  probability for the agreed language, so confidence is not overstated.

No public transcript schema change is required because it already accepts a nullable string
language.

## Test and evidence contract

### Focused automated RED → GREEN

1. Pure segmenter:
   - a 500 ms pause between literal speech plateaus creates two contiguous ranges;
   - a sub-350 ms dip does not split;
   - silence-only and uninterrupted speech each remain one range;
   - no samples are dropped or overlapped.
2. Real client with only external HTTP mocked:
   - enabled auto mode turns a three-leg PCM fixture into three sequential requests without
     `language`;
   - returned text is EN → RU → EN;
   - child segment and word offsets are shifted to literal expected timestamps;
   - aggregate language is `mul` and duration equals the original PCM duration;
   - a child typed failure fails the full logical call;
   - the observer still reports one lifecycle.
3. Bot composition root:
   - `auto` enables segmented auto detection;
   - unset/configured preserves one configured-language request.

Each new test must first fail for the intended missing behavior. No broad suite runs until these
focused tests are green.

### One-variable real hypothesis

Expected: splitting the exact natural Zira → Irina → Zira acceptance audio at its composition
pauses, while keeping the same server, model, endpoint, request fields, and no language pin,
returns recognizable English, Russian, then English.

Only the request boundary changes from one upload to sequential pause-bounded uploads. If any leg
is missing, the hypothesis is false and production implementation stops; no unchanged rerun is
allowed.

### Runtime merge bar

After focused GREEN:

1. build one clean Lite image from the exact candidate SHA;
2. prove image/source/runtime identity and front-door health;
3. run separate natural EN, separate natural RU, and exact EN → RU → EN through the real product
   STT boundary with no language pin;
4. only if all multilingual rows are green, run the Google Meet human-bar witness;
5. run the repository gate required by the delivery constitution;
6. record Expected → Actual → Verdict and what was not checked.

## Stop conditions

Stop and preserve `BLOCKED_PRODUCT` if:

- the one-variable real segmented request still loses a language leg;
- focused tests reveal that offsets or logical request lifecycle cannot remain contract-correct;
- the clean integrated image cannot be tied to the tested SHA;
- any multilingual acceptance row fails.

Google Meet remains `BLOCKED_PREREQUISITE`, not failed, until all preceding multilingual evidence is
green. No branch is pushed and `main` is not advanced before the merge bar is satisfied.
