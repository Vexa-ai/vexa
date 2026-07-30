# deploy/lite/tests — Lite deployment checks

## Source-tree regressions

- `test_local_stt_healthcheck.py` — Docker-free generated-command regression proving the local
  Whisper Python health override is exact-opt-in and preserves the complete upstream argv when
  disabled. On Windows it uses the default WSL distro; set `ALLOY_LITE_TEST_WSL_DISTRO` to select
  an explicit test distro.
- `test_source_identity.py` — temporary-Git-repository checks for clean, staged, unstaged,
  deleted, untracked, ignored-cache, executable-bit, symlink, unmerged-index, and
  Windows-pointer/cross-Git stat-cache source identity behavior, plus isolation from inherited
  gate-level Git routing.
- `test_lite_provenance.py` — Docker-free Make and fake-Docker lifecycle checks for exact source
  labels, exact opt-in, immutable image selection, source-drift and label-mismatch refusal,
  RepoDigest launch/fail-closed behavior, `MATCH`/`STALE`/`LEGACY`/`UNHEALTHY` status details,
  stopped or missing containers, volume/image-preserving down commands, and disabled negative
  controls. These tests do not claim a live Docker runtime.

## Published-image smoke tests

- `concurrent-bots.sh` — the release smoke test and the **sole issuer** of the
  `release/vm-validated` commit status: ≥2 concurrent bots must reach `joining`
  on per-bot profile dirs with zero Chromium SingletonLock signatures (the #478
  failure class fires at browser launch, so no meeting admission is needed).
  Runs in CI as a `release-images / validate-lite` step against the published image, and
  on any clean host after `IMAGE_TAG=vX.Y.Z make lite`; post the attestation with
  `POST_STATUS=1 GIT_SHA=<released sha>` (sole issuer of `release/vm-validated`).
- `test_local_stt_healthcheck.py` — generated-command regression proving the local Whisper Python
  health override is exact-opt-in and preserves upstream behavior when disabled.
