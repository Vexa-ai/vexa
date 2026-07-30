# Alloy Lite runtime provenance evidence

**Date:** `2026-07-30`
**Branch:** `agent/alloy-lite-runtime-provenance-ready-20260730`
**Base revision:** `e71cc95cf013e609c24c4966499002c128cc5454`
**Worktree:** `F:\vexa-alloy-lite-provenance-ready`
**Historical source-reaction fixture:** `B`
**Overall verdict:** `IN PROGRESS`

No credential, API token, cookie, meeting invite, or captured audio is recorded here.

## Evidence altitude

Tasks 1–6 below are historical observations from the earlier
`agent/alloy-lite-runtime-provenance-20260729` worktree. They prove the mechanism's previous
`MATCH → STALE → MATCH`, sidecar preservation, and real STT smoke, but they do **not** prove the
current final diff because code and documentation changed afterward. Current-candidate evidence
starts at Task 7. Final live readback belongs in the external observation bundle so recording it
cannot make the fingerprinted tree stale after the build.

## Task 1 — isolated starting point

### Expected

The session owns one clean worktree and feature branch based on the current protected `main`.
The existing Lite gate and focused Lite tests are green before implementation.

### Actual

- Worktree: `F:\vexa\.worktrees\alloy-lite-runtime-provenance`.
- Branch: `agent/alloy-lite-runtime-provenance-20260729`.
- Base: `d887e0f2333ba8a1d8ce360827bd19809ad45227`.
- `node scripts/gates.mjs lite-makefile` returned `✅ gates green`.
- The pre-change focused Lite unittest set returned `Ran 14 tests ... OK`.
- Repository-wide unittest discovery was not used as a substitute for the canonical gate: the
  bare WSL Python lacks the optional `pytest` package required by `test_env_file_hygiene.py`.

### Verdict

`PASS` — the implementation started from an isolated, known-green Lite boundary. The missing
optional test-runner package is recorded as an environment constraint, not as a product failure.

## Task 2 — source identity and lifecycle RED → GREEN

### Expected

Tests fail before the source identity and lifecycle owner exist, then pass only after the
implementation binds source SHA, dirty state, fingerprint, immutable image, container, and status.
Disabled Alloy configuration preserves the legacy image-selection command.

### Actual

- Initial source-identity run: four tests failed because `source-identity.sh` did not exist.
- Initial lifecycle run: Make-label, immutable-image, and runner tests failed before
  `provenance.sh` and the guarded Make path existed.
- A new negative test proved that `ALLOY_LITE_PROVENANCE=1` in `.env` incorrectly enabled the
  guarded path; after the resolver was limited to explicit Make/ambient configuration, the test
  passed.
- A new JSON-status test failed with missing `container_id`; after status read Docker's real
  `{{.Id}}`, it passed.
- The gate mutation that replaces the provenance mode label with `broken` makes
  `node scripts/gates.mjs lite-makefile` fail, proving the new tests are enforced by the gate.
- The focused changed-boundary set returned `Ran 27 tests ... OK` before the WSL worktree
  regression was added; the complete final count is recorded in the final-gate task below.

### Verdict

`PASS` — the implementation has observed RED controls for each new contract and focused GREEN
coverage, including the flag-disabled negative path.

## Task 3 — Windows-created worktree in Ubuntu WSL

### Expected

The identity and nested Make paths work when Windows Git wrote a `.git` pointer such as
`F:/vexa/.git/worktrees/...`, while native worktrees continue using ordinary Git discovery.

### Actual

- The first real WSL identity command returned
  `source-identity: ... is not a Git worktree`.
- A temporary linked-worktree regression reproduced the Windows-style pointer and failed.
- The identity owner now translates only a matching drive-root pointer to `/mnt/<drive>/...` and
  uses explicit Git dir/work-tree arguments. The regression returned `OK`.
- Per-file Git process spawning exceeded 60 seconds on the real 1,956-file tree. Bounded
  256-file hash batches reduced a repeated real-tree identity run to approximately 13 seconds,
  with an identical repeated fingerprint.
- The public root targets and lifecycle runner now pass the same explicit `ROOT` and `ENV_FILE`
  into every nested Make call. A generated-command regression proves this.

### Verdict

`PASS` — the required Windows → Ubuntu WSL worktree path is functional and bounded without a
hidden keepalive.

## Task 4 — legacy runtime negative control

### Expected

The pre-existing app container has no new lifecycle labels, so `make lite-status FORMAT=json`
returns `LEGACY`, includes its actual image/container IDs, and exits nonzero without altering it.

### Actual

Initial runtime inventory:

| Runtime object | ID |
|---|---|
| app container | `b716d9f6a1b25eb6486558131c6de3d9e17ebd48b7ec9268179ff02f29a13c5c` |
| app image | `sha256:a1c7c24b0af819856f517b6fc88dbb0c4408e8bcb8bcc9f9d5cff75d6e444b38` |
| PostgreSQL container | `70eba0fb5e633d630aaf0a2cffb0e5c43b31bb7684ec3e44f6495b0234d6b635` |
| MinIO container | `0aec47815e81b217113950d025379f324f79ff0788d4c0fd82d4a97f094f53ff` |
| Whisper container | `71dfd6bf5e1f3f973782f1242db352434762a7cd86d222f2d776bf1e98a4f473` |
| Lite network | `a6143a3e3dda0cb6cc427fa06c983b9e3d9431fd4b57738f0544479c0eb9ce6c` |

Existing volumes: `vexa-lite-pgdata`, `vexa-lite-miniodata`, and
`vexa-lite-whisper-cache`.

`make lite-status FORMAT=json ENV_FILE=/mnt/f/vexa/.env` returned:

```json
{"verdict":"LEGACY","mode":"","source_revision":"","source_dirty":"","source_fingerprint":"","expected_image":"","image_id":"sha256:a1c7c24b0af819856f517b6fc88dbb0c4408e8bcb8bcc9f9d5cff75d6e444b38","container":"vexa-lite","container_id":"b716d9f6a1b25eb6486558131c6de3d9e17ebd48b7ec9268179ff02f29a13c5c","health":"healthy"}
```

Exit was nonzero. Container and volume state was not changed.

### Verdict

`PASS` — the old running code is now visibly rejected instead of being mistaken for the current
checkout.

## Task 5 — live fixture A

### Expected

`make lite-dev` builds the current dirty fixture A, replaces the app from the exact image ID,
preserves PostgreSQL/MinIO/Whisper container IDs and volumes, and finishes with `MATCH`. HTTP,
Terminal, and a real local-STT WAV smoke remain functional.

### Actual

- Fixture A source identity:
  - revision: `d887e0f2333ba8a1d8ce360827bd19809ad45227`;
  - dirty: `1`;
  - fingerprint: `53736963d679c6eaac7a31f96ab4b41d1d134e2d197ac48463074b400ca9bfa5`.
- Built image ID:
  `sha256:38f66242bb8c7b7561736c85ccb4b742791c97349860e6ea544ac8501644f3fa`.
  Its three source labels exactly match fixture A.
- New app container ID:
  `9c16d28412f4c2a188b060c3383d6a7e7ae7158fbaed3474d2d575cbd4e1390b`.
- `make lite-status FORMAT=json` returned `MATCH`, the expected and actual image values were the
  same exact image ID, and app health was `healthy`.
- PostgreSQL remained
  `70eba0fb5e633d630aaf0a2cffb0e5c43b31bb7684ec3e44f6495b0234d6b635`;
  MinIO remained
  `0aec47815e81b217113950d025379f324f79ff0788d4c0fd82d4a97f094f53ff`;
  Whisper remained
  `71dfd6bf5e1f3f973782f1242db352434762a7cd86d222f2d776bf1e98a4f473`.
  The three volume names and mountpoints also remained unchanged.
- `make -C deploy/lite test` returned green for gateway `:8056`, agent API `:8100`, and Terminal
  `:3001`.
- `make -C deploy/lite stt-smoke` sent a real generated WAV to local Whisper. The response reported
  language `en`, duration `2.7846875`, and text
  `The quick brown fox jumps over the lazy dog.`; the smoke returned green.
- The invoking `lite-dev` WSL process had exited before the persistence interval. Windows HTTP
  probes, which did not start or keep Ubuntu alive, returned 200 from all three front doors at
  `2026-07-29T06:18:47+02:00` and again at `2026-07-29T06:23:59+02:00`.

### Verdict

`PASS` — fixture A bound the dirty checkout to the exact image and app container, preserved all
sidecars/data, served the three user-facing surfaces, transcribed a real WAV, and survived beyond
the invoking WSL process.

## Task 6 — source reaction and fixture B

### Expected

Changing this evidence marker from `A` to `B` makes status return `STALE`. A second `lite-dev`
produces a different fingerprint, image ID, and app container ID, preserves the sidecars, and
returns `MATCH`.

### Actual

- After the marker changed to B, `make lite-status FORMAT=json` exited nonzero with:
  - verdict: `STALE`;
  - current fingerprint:
    `5a6df6e24d093d27b863d45cd63cef5f2450d0d7bdd1bc1e7f94b014d7c4ce57`;
  - still-running fixture A image:
    `sha256:38f66242bb8c7b7561736c85ccb4b742791c97349860e6ea544ac8501644f3fa`;
  - still-running fixture A container:
    `9c16d28412f4c2a188b060c3383d6a7e7ae7158fbaed3474d2d575cbd4e1390b`.
- The second `lite-dev` consumed fingerprint
  `5a6df6e24d093d27b863d45cd63cef5f2450d0d7bdd1bc1e7f94b014d7c4ce57`
  and built image
  `sha256:b28403064b8b1725a2dec6fc39b1e0ea986ddd3560186d60aab379afe875ea27`.
- It replaced the app with container
  `5f6db047101783c90f60c1d5cf07ce33db6701cd5726543f401f8b781d00ca47`.
  The runner's final human status returned `MATCH`, identical expected/actual image IDs, and
  `healthy`.
- PostgreSQL, MinIO, Whisper, and all three volume IDs/mounts remained identical to the initial
  inventory. Gateway, agent API, and Terminal returned green again.
- The subsequent contract-audit edits intentionally make the live fixture B image stale. They are
  included in the final source candidate and require the final exact-candidate rebuild recorded by
  the delivery readback after this evidence file is frozen.

### Verdict

`PASS` — a real source change was detected before rebuild; rebuild changed the fingerprint, image,
and app container while preserving stateful sidecars, then restored `MATCH`.

## Task 7 — exact candidate gates

### Expected

The focused tests, canonical Lite gate, bounded Lite build, complete Linux gate, and
`git diff --check` are green. The worktree is clean after the authorized local commit. No branch
is pushed and no PR is opened in this session.

### Actual

- A new session-owned worktree was created from
  `e71cc95cf013e609c24c4966499002c128cc5454`.
- The original 7 modified + 6 untracked provenance files were transferred from the historical
  worktree with `mismatch_count=0` across all 13 SHA-256 comparisons.
- A stale pre-existing mutation fixture was observed RED on the current Dockerfile, updated to its
  current final-stage anchor, then returned the real intended runtime-parity RED; the complete
  mutation file returned `22/22` green under Git Bash.
- `.pnpm-store/` is ignored by Git and the Lite Docker context. `git check-ignore` resolves the
  probe to the root `.gitignore`, and `pnpm store path` reports `F:\.pnpm-store\v11`.
- `test_source_identity.py` currently returns `Ran 10 tests ... OK`, including staged/unstaged,
  deleted, symlink, unmerged-index, ignored-cache, and full Windows-pointer identity cases.
- A clean Windows Git stat refresh initially made WSL `diff-files --name-only` report the entire
  tree as changed. The Windows-pointer regression reproduced the false `dirty:true` fingerprint
  (RED); source identity now uses the porcelain worktree diff and the same cross-Git case is GREEN.
- `test_lite_provenance.py` currently returns `Ran 21 tests ... OK`, including exact opt-in,
  missing `APP_IMAGE`, image-label mismatch, missing RepoDigest, every status verdict, missing and
  stopped containers, positive JSON details, source drift before launch, and `[ALLOY]`-prefixed
  runner diagnostics.
- `bash -n deploy/lite/bin/source-identity.sh` and
  `bash -n deploy/lite/bin/provenance.sh` both returned exit code `0`.
- `node scripts/gates.mjs lite-makefile` returned `gates green`; the mutation suite returned
  `22/22` green after exercising its intentional RED controls.
- Five scoped local commits now contain the candidate. The real Docker witness on the exact
  candidate and the full 35-group Linux gate have not run yet.

### Verdict

`IN PROGRESS` — current unit/fake-Docker contracts are green; runtime and full-gate claims remain
unproven until the later tasks run on the committed exact candidate.
