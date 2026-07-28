# Alloy STT pause-bounded code-switch implementation plan

**Date:** 2026-07-28
**Design:** `docs/superpowers/specs/2026-07-28-alloy-stt-code-switch-design.md`
**Baseline:** `dbe38e243cfb834bda2ba2fb23bd48f7debfd399`
**Execution:** local isolated worktree only; no push

## Objective

Close the remaining Vexa R&D product blocker by proving and then implementing automatic
pause-bounded language re-detection for EN → RU → EN audio under the existing
`ALLOY_STT_LANGUAGE_MODE=auto` opt-in. Preserve upstream behavior byte-for-byte when the flag is
unset or `configured`.

For every task, record Expected → Actual → Verdict. An unexpected result stops the task for
interpretation; do not stack speculative fixes or rerun an unchanged deterministic failure.

## Task 1 — Prove the one-variable backend hypothesis

**Artifacts only:**

- Create under ignored session path:
  `.superpowers/sdd/tmp/alloy-code-switch-20260728/`
- Do not edit production source.

### Step 1: Inventory without mutation

Record the current Git SHA/status, Docker container inventory, running image/model identity, health,
and the exact natural acceptance WAV metadata. Do not restart, recreate, stop, or rename pre-existing
containers.

Expected: one usable multilingual Whisper endpoint can be identified without altering unrelated
runtime state. If not, start a uniquely named, non-restarting disposable container on a unique port
using the existing model cache.

### Step 2: Recreate the exact natural fixture if its prior raw file is absent

Use installed Microsoft Zira and Microsoft Irina voices to produce:

```text
EN: Today we are checking multilingual speech recognition in English.
RU: Теперь мы проверяем многоязычную расшифровку на русском языке.
EN: Today we are checking multilingual speech recognition in English.
```

Normalize each source to mono 16 kHz PCM and concatenate them with a literal 700 ms silence between
legs. Retain each leg and the mixed WAV in the ignored artifact path. Record voice identities,
sample format, exact durations, and hashes.

### Step 3: Establish the unchanged RED once

Submit the full mixed WAV once with only:

```text
file
model=whisper-1
response_format=verbose_json
```

Expected: reproduce the known missing-language failure. If it unexpectedly passes, stop and explain
the changed runtime/model evidence instead of implementing a redundant fix.

### Step 4: Change one variable

Split the same PCM at the two inserted pause midpoints and submit the three pieces sequentially to
the same endpoint with the same fields and no `language`.

Expected: responses contain recognizable EN, RU, EN in order. Record complete JSON, elapsed times,
request argv without secrets, and a merged diagnostic transcript.

Verdict:

- `PASS_HYPOTHESIS` permits Task 2;
- any missing leg is `FAIL_HYPOTHESIS` and stops production work.

## Task 2 — Add the pause segmenter with strict TDD

**Files:**

- Create: `core/meetings/modules/whisper/src/pause-segmenter.ts`
- Create: `core/meetings/modules/whisper/src/pause-segmenter.test.ts`
- Modify: `core/meetings/modules/whisper/package.json`

### Step 1: RED — qualifying pause

Before the test body, name the break: removing pause detection or choosing a non-pause split would
collapse two speech plateaus into one range or drop samples.

Write literal PCM fixtures and literal expected sample ranges. Cover one 500 ms pause, no gaps or
overlaps, and minimum adjacent chunk duration.

Run:

```powershell
pnpm --filter @vexa/transcribe-whisper exec tsx src/pause-segmenter.test.ts
```

Expected: fail because the production module/export does not exist.

### Step 2: GREEN — minimal segmenter

Implement one Alloy-marked pure function using the design constants. Return contiguous
`{ startSample, endSample }` ranges and preserve one full range for empty, silence-only, short, and
uninterrupted input.

Run the exact test. Expected: green.

### Step 3: RED/GREEN — negative controls

Add one failing behavior at a time:

- a short low-energy dip does not split;
- silence-only input stays one range;
- uninterrupted speech stays one range;
- three speech plateaus yield three ranges whose lengths sum to the original PCM length.

Run the exact test after each RED and minimal GREEN. Add it to the package test chain only after all
cases pass.

## Task 3 — Segment, request, and merge in the STT client with strict TDD

**Files:**

- Create: `core/meetings/modules/whisper/src/auto-language.test.ts`
- Modify: `core/meetings/modules/whisper/src/transcription-client.ts`
- Modify: `core/meetings/modules/whisper/package.json`

### Step 1: RED — observable end-to-end adapter behavior

Name the break: ignoring the opt-in or merging child results without offsets would make one request,
lose a language leg, or return timestamps relative to each child.

Use the real `TranscriptionClient`, real WAV encoding, real pause segmenter, and a fetch double only
for the external HTTP service. Mirror complete verbose responses. Assert literal:

- three sequential calls;
- no `language` form part;
- EN → RU → EN text;
- shifted segment and word timestamps;
- `duration` from original samples;
- aggregate `language === 'mul'`;
- one observer started/finished lifecycle.

Run:

```powershell
pnpm --filter @vexa/transcribe-whisper exec tsx src/auto-language.test.ts
```

Expected: fail because the config and segmented merge do not exist.

### Step 2: GREEN — minimal client behavior

Add an Alloy-marked internal config boolean. Under that boolean and absent a forced language:

1. calculate ranges;
2. execute child calls sequentially inside the existing logical request slot;
3. pass the caller prompt only to the first child;
4. merge results according to the design contract.

Keep `sendRequest` and retry behavior as the only HTTP path; do not duplicate multipart or error
translation.

Run the exact test. Expected: green.

### Step 3: RED/GREEN — failure and rollback controls

Add and observe separate RED cases:

- a child typed failure rejects the entire logical request;
- agreed child languages return that language and conservative confidence;
- no qualifying pause produces one request;
- disabled config with a forced language and prompt produces exactly the original one request.

Implement only what each RED requires, then rerun the exact test.

### Step 4: Focused module regression

Run:

```powershell
pnpm --filter @vexa/transcribe-whisper test
pnpm --filter @vexa/transcribe-whisper build
pnpm --filter @vexa/transcribe-whisper check:isolation
```

Expected: all green before touching bot wiring.

## Task 4 — Wire only the existing Alloy auto flag

**Files:**

- Modify: `core/meetings/services/bot/src/pipeline.test.ts`
- Modify: `core/meetings/services/bot/src/pipeline.ts`

### Step 1: RED — composition behavior

Name the break: `auto` omitting the language but failing to enable pause-bounded re-detection would
reintroduce the original one-language-per-window defect.

Extend the existing real-client/fetch boundary case with a literal three-plateau PCM input:

- `ALLOY_STT_LANGUAGE_MODE=auto` produces three no-language requests;
- unset mode with invocation language produces one language-bearing request.

Restore environment and fetch in `finally`.

Run:

```powershell
pnpm --filter @vexa/bot exec tsx src/pipeline.test.ts
```

Expected: the auto count fails before wiring.

### Step 2: GREEN — one configuration hop

Pass the client opt-in only when normalized mode equals `auto`. Keep the configured language
selection and all other flags unchanged. Mark the block `ALLOY:`.

Rerun the exact bot test. Expected: green.

### Step 3: Focused bot regression

Run:

```powershell
pnpm --filter @vexa/bot build
pnpm --filter @vexa/bot check:isolation
```

Do not run the broad bot package test chain unless the exact pipeline case and Whisper module are
green.

## Task 5 — Update operator documentation

**Files:**

- Modify: `docs/ALLOY-CUSTOMIZATIONS.md`
- Modify: `deploy/lite/README.md`
- Modify: `core/meetings/modules/whisper/README.md`
- Create: `docs/changelog.d/local-alloy-stt-code-switch.md`

Document:

- `auto` now re-detects at qualifying natural pauses;
- no language pin is sent;
- child calls are sequential and share one logical limiter lifecycle;
- `mul` aggregate metadata for mixed windows;
- switches without an acoustic pause remain backend-limited;
- exact rollback is `configured`/unset plus Lite restart;
- enabled behavior may increase request count and CPU latency for pause-rich windows.

Do not edit `docs/docs/changelog.mdx`.

Run only the focused docs/current and module checks named by repository tooling, then inspect
`git diff --check`.

## Task 6 — Focused integrated verification

Run in order, stopping at the first red:

```powershell
pnpm --filter @vexa/transcribe-whisper test
pnpm --filter @vexa/transcribe-whisper build
pnpm --filter @vexa/transcribe-whisper check:isolation
pnpm --filter @vexa/bot exec tsx src/pipeline.test.ts
pnpm --filter @vexa/bot build
pnpm --filter @vexa/bot check:isolation
```

Then run the narrow repository gates that own the changed boundaries. Record complete commands and
raw summaries. Do not promote a diagnostic pass to an official gate pass.

Review the diff for:

- every Alloy-owned code block marked `ALLOY:`;
- every Alloy diagnostic prefixed `[ALLOY]`;
- no new public/runtime coupling outside the STT adapter;
- no upstream behavior change with the flag disabled;
- no unrelated refactor;
- tests that would fail for a missing split, missing language omission, wrong offset, partial error,
  or broken rollback.

Commit focused source/docs only after green.

## Task 7 — Clean image and real multilingual product evidence

Create a uniquely named ignored runtime evidence directory and uniquely named disposable runtime
resources. Never mutate or adopt pre-existing containers.

### Step 1: Clean image identity

Build Lite from the exact candidate SHA with the approved explicit Alloy pilot flags and
multilingual `Systran/faster-whisper-small`. Record source SHA, context hash, image ID, build log,
licenses, and image contents that prove the changed bot/Whisper module is present.

### Step 2: Isolated runtime

Start only unique, non-restarting PostgreSQL, MinIO, Whisper, and Lite resources. Poll bounded
health. Prove running image IDs and environment without printing secrets.

### Step 3: Product STT acceptance

Through the real bot/STT adapter, not a direct diagnostic script, run:

1. natural English;
2. natural Russian;
3. the exact natural EN → RU → EN fixture.

No request may contain `language`. The mixed result must contain recognizable content from all
three legs in order and honest `mul` aggregate metadata. Record request count, timings, offsets,
runtime logs, and raw response artifacts.

Any missing/empty/reordered leg is `FAIL / BLOCKED_PRODUCT` and stops before Meet.

## Task 8 — Google Meet witness, final gates, and integration

Only after Task 7 is green:

1. use the available signed-in browser session for one disposable Google Meet;
2. run join → real audio → Whisper → Redis/API → Terminal telemetry;
3. record an aligned evidence bundle or an honest `BLOCKED_EXTERNAL` if login/admission is
   unavailable;
4. run `node scripts/gates.mjs all` only after every focused gate is green;
5. write
   `docs/superpowers/evidence/2026-07-28-alloy-stt-code-switch.md` with Expected → Actual → Verdict,
   raw evidence, and explicit omissions;
6. commit the evidence locally;
7. advance `main` only if the design merge bar, multilingual product evidence, Meet requirement,
   and full repository gate all pass.

Before advancing `main`, prove ancestry and clean state. Use fast-forward only. Delete the obsolete
R&D branch/worktree only after proving its tip is an ancestor of the retained branch. Do not push.
