# Alloy Vexa R&D merge-readiness design

**Date:** 2026-07-28
**Status:** Approved direction; implementation awaits written-spec review
**Starting revision:** `f4f652b80d5ef6bd83a98364f8344599bc16f8bd`

## Objective

Make the existing repository-local Alloy Vexa R&D line safe to fast-forward into `main`, then
return `F:\vexa` to a single primary `main` checkout without losing the two unrelated side branches
or their worktrees.

The required product evidence is:

1. a tracked-source Vexa Lite image built with the existing exact opt-in
   `ALLOY_LITE_BUNDLED_PYTHON=1`;
2. provenance that ties the resulting image to the tested Git revision;
3. real English, Russian, and English-to-Russian-to-English transcription with
   `Systran/faster-whisper-small` and no request-level language pin;
4. a Google Meet end-to-end witness, or an honest `BLOCKED_EXTERNAL` result when the required
   signed-in external session is unavailable.

## Current facts

- `main` is an ancestor of the R&D revision, so successful integration can remain fast-forward
  only.
- The R&D line has focused implementation evidence, but clean image provenance, multilingual
  behavior, and the Google Meet human bar are still open.
- The last full gate is not a valid pass. It exposed both Windows-local baseline/tooling failures
  and one R&D-owned failure: `deploy/lite/Dockerfile.lite` pins
  `python:3.12-slim-bookworm`, but `image-licenses.json` does not declare that base image.
- An earlier clean-context build completed all five Python environments but lost its WSL process
  tree during cleanup before an image tag, digest, labels, or source hashes were proven. Repeating
  the same uncontrolled run would not add evidence.

## Scope

In scope:

- declare the pinned Python base image at the existing `image-licenses.json` ownership boundary;
- preserve the current exact-`1` Lite fallback and its flag-off upstream behavior;
- run focused contract and governance checks before an expensive build;
- perform one bounded, tracked-only Lite build and record its provenance;
- perform the multilingual Whisper and Google Meet acceptance runs;
- classify every check as `PASS`, `FAIL`, `BLOCKED_ENV`, or `BLOCKED_EXTERNAL`;
- integrate only by fast-forward after the evidence bar is met.

Out of scope:

- production changes outside the standalone `F:\vexa` pilot;
- redesigning upstream Vexa to make its full gate Windows-native;
- changing the default Lite Python path or the default English-only Whisper model;
- hiding or reclassifying deterministic failures as passes;
- modifying, deleting, or adopting either existing side worktree;
- pushing, opening a PR, or touching running containers that predate this session.

## Design

### 1. Fix the packaging contract at its owner

Add `python` as a declared pinned base image in `image-licenses.json`, with the permissive Python
licence and an explanation of its exact opt-in Lite use. Do not special-case the gate and do not
duplicate licence metadata in the Dockerfile.

This keeps one source of truth for image policy. It applies DRY and SOLID proportionately: no
duplicated allowlist, one clear manifest responsibility, no coupling between the license gate and
the Alloy build flag, and an independently testable rollback path.

### 2. Close cheap evidence before expensive evidence

Run the exact image-license gate and its adversarial tests first. Then run the existing bundled
Python, Alloy opt-in, and local-Whisper healthcheck suites plus the focused configuration,
architecture, and packaging gates that cover the changed surface.

Any focused red result stops the build. Baseline failures outside the R&D diff remain recorded
separately and do not become product failures or passes.

### 3. Build once from controlled source

Create a tracked-only build context from the tested commit. Use one unique image tag and exact
`ALLOY_LITE_BUNDLED_PYTHON=1`. Before invoking WSL, inspect its current process state. If a Docker
keepalive is necessary, start at most one, record its Windows PID, reuse it for the entire run, and
terminate only that recorded PID during cleanup.

The build has a 15-minute limit. Preserve its complete log and periodic progress tail. Do not
remove, restart, or rename pre-existing containers.

Success requires all of:

- a locally addressable image under the unique tag;
- a recorded immutable digest or image ID;
- OCI revision and ref-name labels matching the tested commit and tag;
- hashes of the relevant tracked build inputs matching the source snapshot;
- confirmation that the exact opt-in path created the five service environments.

Missing any item is `FAIL` or `BLOCKED_ENV`, never a partial build pass.

### 4. Prove behavior on the built artifact

Start the local STT backend with
`WHISPER_MODEL=Systran/faster-whisper-small`. Submit three bounded real samples: English, Russian,
and English-to-Russian-to-English. The requests must omit the `language` field. Record the backend
model, request shape, response, non-empty text, and language coverage actually present in the
transcript.

Configuration alone is not evidence. Empty text, a missing language segment, an unexpected model,
or a request-level language pin is `FAIL`.

Only after multilingual success, run the normal Google Meet path against the new image. Use product
endpoints and aligned API, Redis, bot-log, and Terminal observations. A missing signed-in meeting
session is `BLOCKED_EXTERNAL`; a started product flow that fails acceptance is `FAIL`.

### 5. Integrate and clean up conservatively

After focused checks, provenance, multilingual evidence, and the Google Meet result are recorded,
run one fresh full gate. Report its exact results and keep known environment-specific failures
explicit; do not rerun unchanged deterministic reds.

Use scoped commits. Fast-forward the R&D branch to the verified commit, then fast-forward `main`.
Switch the primary `F:\vexa` checkout to `main`. Remove only this session's temporary worktree and
temporary branch after proving they are integrated. Preserve the review-fixes and
Whisper-healthcheck branches and worktrees. Do not push.

## Stop conditions

Stop and interpret rather than retry unchanged when:

- a focused contract test is red;
- the tested tree differs from the tracked-only build input;
- WSL or Docker ownership cannot be established safely;
- the build reaches 15 minutes without producing the required artifact;
- the WSL process tree disappears again;
- image provenance is incomplete;
- multilingual output fails any of the three samples;
- the Google Meet flow starts but product evidence stalls for 15 minutes;
- fast-forward ancestry is no longer true or an affected checkout becomes dirty.

## Completion contract

The R&D line is merge-ready only when the packaging defect is fixed, focused checks are green,
the clean image and multilingual evidence pass, and Google Meet is either passed or explicitly
`BLOCKED_EXTERNAL`. The final report must distinguish those outcomes from the full-gate baseline,
list what was not checked, and state that no push occurred.
