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
