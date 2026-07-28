# Alloy STT pause-bounded code-switch evidence

**Date:** `2026-07-28`
**Branch:** `alloy/vexa-rnd-code-switch-20260728`
**Candidate commit:** `59bc7e925cf9ee173fd3262963b7b47c6d3c1bcd`
**Candidate tree:** `d8988e3b8ae6415cd58180881c3922e068b8faab`
**Overall verdict:** `BLOCKED_PRODUCT`
**Merge verdict:** keep the local branch/worktree; do not move `main`

## Summary

The opt-in pause-bounded adapter works through the real bot/STT boundary for natural English,
natural Russian, and an EN → RU → EN WAV. The mixed direct product result contains all three legs
in order, reports `language: mul`, and keeps every timestamp inside the original audio duration.

The exact clean candidate also passes the full Linux repository gate. The final Google Meet human
bar is red, however. Google Meet transported the disposable guest audio to the Vexa listener and
Whisper detected both English and Russian, but the finalized Meeting API text did not preserve
recognizable EN → RU → EN content. Three bounded one-variable levels established a too-quiet region
and a clipping region without finding a valid witness. The stop threshold was then enforced.

The live witness is part of the merge bar. Therefore the implementation is not eligible for
fast-forward into `main`, even though focused checks, clean-image product checks, and the full
repository gate are green.

No push occurred. No credential, API key, cookie, account identifier, or meeting invite code is
recorded here.

## Task 1 — one-variable backend hypothesis

### Expected

The unchanged full natural EN → RU → EN WAV loses at least one language leg when sent as one
un-pinned Whisper request. Splitting only at the two known 700 ms pause midpoints and sending the
three parts sequentially returns recognizable EN, RU, EN.

### Actual

The natural fixtures used installed Microsoft voices and mono 16 kHz PCM:

| Fixture | Duration | SHA-256 |
|---|---:|---|
| English | 4.3036875 s | `a69dba801844e652c61fb7ee552bd909faadc859e9195370f7e737429f1b8788` |
| Russian | 5.3635625 s | `3b6e19e1c88c04392067e524c22770ed75bebb522c8ab41d4b0d225aed474005` |
| EN → RU → EN | 15.3709375 s | `fa33da1e2ef2b11b88dd52b53698ba379243e007914684b3b255cb25f103c7c2` |

The one-request control detected Russian and lost both English legs. The same PCM split only at the
pause midpoints returned, in order:

```text
Today we are checking multilingual speech recognition in English.
Теперь мы проверяем многоязычную расшифровку на русском языке.
Today we are checking multilingual speech recognition in English.
```

No request contained a `language` field.

### Verdict

`PASS_HYPOTHESIS`. Pause-bounded re-detection was justified; production TDD could proceed.

## Task 2 — TDD implementation and rollback contract

### Expected

Under `ALLOY_STT_LANGUAGE_MODE=auto` only:

1. qualifying natural pauses split PCM into contiguous ranges;
2. child requests remain sequential inside one logical limiter lifecycle;
3. no language pin is sent;
4. only the first child receives the caller prompt;
5. text, segments, and words merge with bounded offsets;
6. mixed child languages aggregate to `mul`;
7. a child failure fails the whole logical request;
8. unset or `configured` mode preserves the original one-request behavior.

### Actual

Strict RED/GREEN tests were observed for the pure pause segmenter, the real
`TranscriptionClient`/fetch boundary, bot composition, child failure, same-language aggregation,
no-pause input, disabled rollback, and shifted word/segment offsets.

The first real mixed product run exposed an additional defect: a backend segment could extend past
its child PCM range, producing a merged end timestamp of `16.36` for a `15.3709375` second input.
A synthetic RED reproduced `4.75 > 4.0`; the minimal fix clamps child segment and word timestamps
to their source range and clamps the aggregate to the logical duration. The final real product
result stayed within `15.3709375`.

Relevant commits:

```text
4105ba4 docs: design Alloy STT code-switch pilot
3c130d5 feat(stt): re-detect language at natural pauses
59bc7e9 fix(stt): bound merged segment timestamps
```

The change keeps one responsible STT-adapter boundary, reuses the existing request/retry path, and
separates opt-in configuration from processing. This applies DRY and SOLID proportionately without
adding a parallel HTTP implementation or changing unrelated upstream contracts.

### Verdict

`PASS`. The opt-in and rollback contracts are covered, and the runtime-discovered timestamp defect
has a RED/GREEN regression.

## Task 3 — focused verification

### Expected

Every changed module and owning narrow gate is green before image or live work.

### Actual

Fresh focused verification completed with exit code 0:

| Command/check | Result |
|---|---|
| `pnpm --filter @vexa/transcribe-whisper test` | green |
| `pnpm --filter @vexa/transcribe-whisper build` | green |
| `pnpm --filter @vexa/transcribe-whisper check:isolation` | green |
| `pnpm --filter @vexa/bot exec tsx src/pipeline.test.ts` | green |
| `pnpm --filter @vexa/bot build` | green |
| `pnpm --filter @vexa/bot check:isolation` | green |
| `node scripts/gates.mjs dataflow` | green |
| `node scripts/gates.mjs readme` | green |
| `node scripts/gates.mjs exports` | green |
| `node scripts/gates.mjs isolation` | green |
| `node scripts/gates.mjs docs-version` | green |

An earlier Windows `gate:node` run passed 22 tasks and failed only because Windows cannot execute
the extensionless fake `aws` file created by the existing remote-browser test. It was not rerun
unchanged on Windows; the later full Linux gate supersedes that environment-specific result.

### Verdict

`PASS`.

## Task 4 — clean image provenance and isolated runtime

### Expected

Build and run a tracked-only candidate with no `.git`, `.env`, or `.pnpm-store`; prove source,
image, runtime, and service identities without touching pre-existing Vexa containers.

### Actual

| Field | Value |
|---|---|
| Source commit | `59bc7e925cf9ee173fd3262963b7b47c6d3c1bcd` |
| Source tree | `d8988e3b8ae6415cd58180881c3922e068b8faab` |
| Snapshot archive SHA-256 | `35398347a19db1655e9df3cb3177cd2fa748451763b3b5e24e29ec93ea414067` |
| Image tag | `vexa-lite:alloy-code-switch-20260728-59bc7e9` |
| Image ID | `sha256:96caccf76e9c861fcf48e90ac0f3476b111646ba9497c9368e76a97b6a97fc8a` |
| Build duration | 471.7 s |
| Build result | exit 0 |

The build used `--no-cache --network=host` and explicit Alloy pilot arguments. OCI revision/ref
labels matched the source and tag. The snapshot contained no `.git`, `.env`, or `.pnpm-store`.
All five bundled service venvs imported `uvicorn` and `ssl` with OpenSSL 3.0.2. The candidate app
was healthy with `restart=no`.

The isolated runtime used only exact `alloy-code-switch-20260728-*` names and unique ports,
network, and volumes. Four pre-existing `vexa-lite*` container IDs were inventoried before the run
and preserved.

### Verdict

`PASS`.

## Task 5 — real product adapter multilingual result

### Expected

Call the built image's actual bot pipeline adapter with the natural English, Russian, and mixed
fixtures. The mixed result must contain recognizable EN → RU → EN, omit language pins on child
requests, report honest aggregate metadata, and keep timestamps within the original duration.

### Actual

The actual `/app/core/meetings/services/bot/dist/pipeline.js` adapter returned:

| Fixture | Text/language result |
|---|---|
| English | exact expected sentence; `en`; duration `4.3036875` |
| Russian | exact expected sentence; `ru`; duration `5.3635625` |
| Mixed | exact EN → RU → EN; `mul`; duration `15.3709375` |

The final mixed segments were bounded as:

```text
0.00–3.50          English
4.35–10.35         Russian
10.36–15.3709375   English
```

Whisper logs showed five successful sequential POSTs for the three logical samples, with detected
languages `en`, `ru`, then `en`, `ru`, `en`. No product request pinned a language.

### Verdict

`PASS`.

## Task 6 — Google Meet human-bar witness

### Expected

Run one real path:

```text
disposable WAV guest
→ virtual microphone
→ Google Meet
→ muted Vexa listener
→ candidate bot adapter
→ Whisper
→ Meeting API transcript
```

The finalized API transcript must contain recognizable EN → RU → EN in order. Aggregate `mul` or
per-emitted-segment language behavior is acceptable. A diagnostic language decision without valid
text is not a pass.

### Actual

The listener joined and became active in three clean meetings. The disposable speaker joined,
unmuted its existing Lite virtual microphone, played the exact mixed WAV, waited for transport,
then muted, left, and cleaned up. The Vexa listener was host-muted before playback.

One repeated disposable guest identity was rejected by Google before it reached the host. Changing
only the one-time guest name produced an admission request immediately; no unchanged retry was
performed.

Three bounded signal-level observations followed:

| Virtual mic | Listener peak | Whisper/API observation | Verdict |
|---:|---:|---|---|
| 61% (`-12.97 dB`) | `0.021` | short low-level chunks; one finalized English hallucination | too quiet |
| 100% (`0.00 dB`) | `1.006` | Whisper detected EN/RU/EN and API emitted `mul`, but finalized text was clipped/garbled and lost the required wording | clipped |
| 85% (`-4.24 dB`) | `0.951` | finalized API text repeated `TODAY`; no recognizable EN → RU → EN | clipped/invalid |

At 100%, server logs contained separate English and Russian detections, including a Russian
decision for the middle window, which proves that the changed auto-language path participated in
the real meeting flow. It does not satisfy the human bar because the finalized text was wrong.

The 85% run was declared the final tuning attempt before it started. Its failure triggered the
stop threshold; no further level search or unchanged rerun was performed.

### Verdict

`FAIL / BLOCKED_PRODUCT`. The real transport and language re-detection path executed, but
recognizable live EN → RU → EN text is not proven.

## Task 7 — full repository gate

### Expected

Run the complete Linux `node scripts/gates.mjs all` against the exact tracked candidate and retain
the numeric exit status.

### Actual

The final run used:

- the clean tracked snapshot for source;
- Linux Node `v22.20.0` from the exact candidate image;
- a read-only repository object database;
- a temporary Git index loaded with candidate commit `59bc7e9`;
- an isolated, subsequently deleted pnpm store.

The temporary Git index matters because `db-budget` uses `git ls-files`. A preceding run without
Git metadata passed every other group but correctly could not classify DB owners. The final run
restored that required observation surface without changing source.

The final command ended with exit code 0 and:

```text
✓ gate:db-budget — Σ 70/100 connections fits [...]
✓ gate:python — 12 package(s) · pytest green
✓ gate:node — 18 package(s) · build + test green
✓ gate:contract-conformance — 2 service(s) conform [...]
✅ gates green
```

All 35 reported gate groups were green. The built-in compose/stress/chaos lanes reported their
declared green-or-skip/opt-in outcomes inside the gate container. Independent isolated Docker
runtime evidence is recorded in Tasks 4–6.

The first clean dependency install stalled in the external `onnxruntime-node@1.26.0` NuGet
postinstall after the package store had populated. The exact gate container was stopped after
process and freshness inspection. The final environment used `pnpm install --ignore-scripts`;
the complete Python and Node build/test groups then ran and passed, so no success is inferred from
installation alone.

### Verdict

`PASS`.

## Task 8 — integration and cleanup

### Expected

Advance `main` only if product adapter, Meet witness, and full gate are all green. Otherwise keep
the branch/worktree and clean only exact session-owned runtime resources.

### Actual

`main` remained clean at:

```text
3ab9af522c61441dccb75f2de274cd26549927e5
```

It was not fast-forwarded because Task 6 is red. The local candidate branch/worktree remains for
the next R&D iteration. No push occurred.

Cleanup removed exactly:

- five `alloy-code-switch-20260728-*` containers;
- one session network;
- three runtime volumes;
- one isolated gate pnpm-store volume;
- the validated WSL keepalive process with PID `44416`.

Post-cleanup inventory found no remaining `alloy-code-switch-20260728` container, network, or
volume. The keepalive PID was absent. The four pre-existing `vexa-lite*` containers retained the
same IDs and remained in their pre-cleanup running states.

The candidate image and ignored source/runtime artifacts were retained for reproducibility.

### Verdict

`PASS` for bounded cleanup and preservation. `NO MERGE` because the required Google Meet witness is
`BLOCKED_PRODUCT`.

# Continuation — isolated Meet audio path and merge candidate

The failed Task 6 result above is retained as historical evidence. This continuation changes the
stand topology, measures each existing tap, and records the later candidate without rewriting that
failure into a pass.

## Task 9 — branch and stand preflight

### Expected

Delete only the obsolete runtime branch after proving its commit is already reachable from the
working R&D line. Keep `main` untouched and perform all file changes in a new worktree inside the
repository.

### Actual

- `main` was clean at `3ab9af522c61441dccb75f2de274cd26549927e5`.
- The prior R&D line was clean at `458e025` and 37 commits ahead of `main`.
- `main` was an ancestor of the R&D line.
- `alloy/vexa-rnd-runtime-next-20260728` at `c1fe497` was an ancestor of that line and was deleted
  with `git branch -d`.
- The continuation ran in its own
  `alloy-rnd-meet-witness-20260728` worktree and branch.

### Verdict

`PASS`. No commit became unreachable and `main` was not changed.

## Task 10 — one-hypothesis audio-boundary isolation

### Expected

Measure the existing path:

```text
source WAV → virtual mic → WebRTC sender → Meet remote stream → Vexa capture → Whisper/API
```

Change one stand variable at a time. Do not change production STT to compensate for damaged input.

### Actual

The source was mono signed 16-bit PCM at 16 kHz:

| Duration | Peak | RMS | Clipping | SHA-256 |
|---:|---:|---:|---:|---|
| `15.370938 s` | `0.672913` | `0.085264` | `0` | `fa33da1e…c7c2` |

The bounded hypotheses were:

1. **Shared PulseAudio graph.** A separate speaker container, profile, and PulseAudio graph removed
   speaker/listener coupling, but default Chromium processing still produced three clipped sender
   samples and 419 clipped Meet-remote samples. The hypothesis explained isolation risk but did not
   close the signal-quality bar.
2. **Chromium AGC/noise suppression/echo cancellation.** Keeping the separate graph and changing
   only these applied constraints from enabled to disabled removed clipping at virtual mic, sender,
   Meet remote, and Vexa capture. Virtual-mic/source correlation was `0.983`, sender/source
   correlation was `0.991`, and Meet-remote/source correlation was `0.897`.
3. **Source format or level.** The exact WAV had valid mono/16 kHz/s16 framing, `peak=0.672913`,
   `RMS=0.085264`, and no clipped sample. The whole pattern survived the Meet remote stream, so this
   hypothesis was disproved.

The first clean-image run after stand calibration still retained only `10.752 s` in Vexa capture
from the `15.370938 s` source and emitted only the middle Russian phrase. That localized the
remaining defect to the product capture/scheduling path rather than Whisper or the test speaker.

### Verdict

`PASS` for localization. Stand distortion was removed without a production change; the remaining
loss was proven at a producer/capture boundary before code was changed.

## Task 11 — producer-boundary RED → GREEN fixes

### Expected

Write a failing regression at each proven point of introduction, then make the smallest
producer-owned change. Preserve the default configured-language behavior and avoid a second audio
path.

### Actual

Two independent RED → GREEN pairs were required:

1. `alloy-channel-backpressure.test.ts` first reproduced an EN → RU → EN same-speaker sequence in
   which the replaceable pending scheduler discarded the middle turn. Commit `37d3f9f` preserved
   every same-speaker window through the existing scheduler and removed batch-relative timestamp
   ownership from that lane.
2. `silence-hangover.test.ts` first failed because the capture gate had no bounded pause state.
   Commit `a0c471c` added one pure `advanceSilenceGate` policy and one per-stream counter to the
   existing `onAudio` path. Loud audio resets a 2,000 ms allowance; silence is emitted only while
   that allowance remains.

The source contains two natural pauses of `1.60137 s` and `1.58425 s`. Both fit within the bounded
hangover. Idle silence still closes after the allowance.

Focused final results:

```text
pnpm --filter @vexa/gmeet-capture exec tsx src/silence-hangover.test.ts
  5/5 cases passed

pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-channel-backpressure.test.ts
  PASS ALLOY same-speaker gaps preserve every code-switch audio window

pnpm --filter @vexa/gmeet-capture test
  all four capture suites passed

pnpm --filter @vexa/capture-codec build
pnpm --filter @vexa/gmeet-capture build
  both exited 0
```

### Verdict

`PASS`. The implementation remains DRY/SOLID at the scale of the fix: one policy, one stream-owned
state, and the existing capture port; no duplicate capture or downstream compensation.

## Task 12 — exact Lite candidate

### Expected

Build a clean tracked snapshot of the exact code candidate, prove its provenance, and run the live
stand from that image.

### Actual

| Property | Observation |
|---|---|
| Candidate | `a0c471ce518258efc2dadffa06e63b998f4b5581` |
| Git tree | `5fa770b61b6107c3688e988faba4b99c1b70152d` |
| Tracked tar SHA-256 | `3ee2578a0301aa67f8e17fcb36b07b2d8058ac5e66965eb3716d52154f3d5a1c` |
| Snapshot exclusions | no `.git`, `.env`, or `node_modules` |
| Confirmed image | `sha256:3f917a98be8fd78f8a3941dc4eb395e8dd7f6e616792cf44c456c4d4a9408297` |
| OCI revision | exact candidate SHA |

The `--no-cache` build completed all 83 Dockerfile steps, registered the image, and supplied the
healthy app used by both live witnesses. Its 15-minute outer watchdog canceled the BuildKit client
after registration while the exporter was still reporting unpack, so that invocation is not
reported as exit-0. A bounded export confirmation from a newly unpacked copy of the same verified
tar reused those clean layers, exported the identical config/layers, and ended with `#84 DONE`.

The exact app answered HTTP 200 at the gateway, terminal, and agent boundaries before either
witness began.

### Verdict

`PASS`. The live artifact is traceable to the exact clean code candidate; the watchdog condition is
recorded rather than hidden.

## Task 13 — two fresh Google Meet witnesses

### Expected

Run two sequential meetings with fresh guest identities. Each finalized API transcript must contain
recognizable EN → RU → EN, honest language metadata, monotonic bounded timestamps, and no transport
clipping, unintended duplicate, or hallucination.

### Actual

Each run used a new Meet room, listener identity, guest identity, Chromium profile, container, and
PulseAudio graph. Applied browser constraints reported AGC, noise suppression, and echo cancellation
disabled. The speaker used the fixed 85% calibrated level.

Boundary measurements:

| Run | Boundary | Duration (s) | Peak | RMS | Clipped samples |
|---|---|---:|---:|---:|---:|
| 1 | source WAV | `15.370938` | `0.672913` | `0.085264` | `0` |
| 1 | virtual mic | `21.012375` | `0.414852` | `0.044690` | `0` |
| 1 | WebRTC sender | `21.000000` | `0.552482` | `0.061598` | `0` |
| 1 | Meet remote master | `326.100000` | `0.522098` | `0.015309` | `0` |
| 1 | Vexa capture | `16.128000` | `0.373174` | `0.047588` | `0` |
| 2 | source WAV | `15.370938` | `0.672913` | `0.085264` | `0` |
| 2 | virtual mic | `21.029812` | `0.409486` | `0.044671` | `0` |
| 2 | WebRTC sender | `20.940000` | `0.548602` | `0.061776` | `0` |
| 2 | Meet remote master | `174.480000` | `0.539183` | `0.020847` | `0` |
| 2 | Vexa capture | `15.616000` | `0.388199` | `0.047322` | `0` |

Meet-remote duration includes the admitted listener's full room lifetime; its peak, RMS, clipping,
and PCM are nevertheless measured from the actual product recording. Vexa capture retained the
speech plus bounded pause allowance rather than the earlier `10.752 s` truncated signal.

Both finalized API results were:

```text
Today we are checking multilingual speech recognition in English.  [mul]
Теперь мы проверяем многоязычную расшифровку на русском языке.     [mul]
Today we are checking multilingual speech recognition in English.  [en]
```

Run 1 relative intervals were `0.000–4.000`, `4.160–9.160`, and `9.160–16.128`.
Run 2 relative intervals were `0.000–3.560`, `4.320–9.320`, and `9.320–15.616`.
The repeated English wording is present in the source as its first and third phrases; the API added
no unintended repeat.

Sanitized PCM, boundary statistics, transcripts, build logs, and gate logs are retained under:

```text
.superpowers/sdd/runtime-evidence/alloy-meet-audio-20260728/
```

Meeting URLs, native meeting IDs, account details, keys, and captured-signal headers are excluded.

### Verdict

`PASS`. The live human bar is green twice on the exact candidate.

## Task 14 — final Linux gate

### Expected

Run `node scripts/gates.mjs all` in Linux against the exact tracked candidate with a temporary Git
index. Treat transport or worker failures as failures, even when every assertion is green.

### Actual

The first full run used the exact source through a Windows p9 bind mount. It is retained as a
negative control, not a pass:

```text
@vexa/terminal:
  Test Files 48 passed (48)
  Tests      332 passed (332)
  Errors     11 worker-response timeouts
  Duration   490.39 s
```

Changing only the source filesystem to a native Docker volume produced:

```text
pnpm --filter @vexa/terminal test
  Test Files 59 passed (59)
  Tests      434 passed (434)
  Duration   29.28 s
```

A new native source volume was then populated from the verified tracked tar. Dependencies were
linked from the isolated session store with lifecycle scripts disabled; `.git` was read-only and a
temporary index was loaded with candidate `a0c471c`. The full run ended with:

```text
✓ gate:db-budget — Σ 70/100 connections fits [...]
✓ gate:python — 12 package(s) · pytest green
✓ gate:node — 18 package(s) · build + test green
✓ gate:contract-conformance — 2 service(s) conform [...]
✅ gates green
```

All 35 reported groups were green, including their declared green-or-skip/opt-in outcomes. No failed
run is presented as an official pass.

### Verdict

`PASS`. Focused tests, live witnesses, exact-image provenance, and the complete Linux gate are green.
