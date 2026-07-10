# Releasing Vexa — the machinery and its contract

A release is **the whole image set at one commit**, never a single image:
`vexaai/vexa-lite` (amd64+arm64) · `vexaai/v012-{gateway,admin-api,meeting-api,runtime,
agent-api,agent-worker,mcp,terminal}` (amd64) · `vexaai/vexa-bot` (amd64). Compose builds
from source, but k8s/Helm and `make lite` consume these published images — an incomplete
set is a broken deployment path.

## The sequence (enforced, not remembered)

| Step | Command | Enforced by |
|---|---|---|
| 1. Release PR merges | normal PR flow | branch protection: `gates` + **`release/vm-validated`** |
| 2. Build + push the set | `make release TAG=vX.Y.Z` | script refuses dirty trees; stamps OCI `revision/version/created/source` labels on every image |
| 3. Smoke the PUBLISHED image | `IMAGE_TAG=vX.Y.Z make lite` on a clean host, then `deploy/lite/tests/concurrent-bots.sh` | ≥2 concurrent bots on isolated profiles (the #478 class); this script is the **sole issuer** of `release/vm-validated` |
| 4. Push the tag — the release button | `git push origin vX.Y.Z` | `release` workflow: **gate:release-set** (`deploy/release/verify-set.sh`) refuses the Release unless every image exists at the tag with `revision == tagged commit`; then creates a **DRAFT** Release |
| 5. Publish the Release text | human, in the GitHub UI | drafts don't announce themselves |

`:latest` is repointed only after step 3 is green (`docker buildx imagetools create -t
vexaai/vexa-lite:latest vexaai/vexa-lite:vX.Y.Z`) and is watched by the daily
`latest-drift` workflow — red if it lags main by >7 days or carries no revision label.

## The `release/vm-validated` contract

- **What it attests:** the *published* lite image was pulled onto a clean host and ran
  ≥2 concurrent bots to `joining` with per-bot profile dirs and zero SingletonLock
  signatures, for the full observation window.
- **Sole issuer:** `deploy/lite/tests/concurrent-bots.sh` (with `POST_STATUS=1
  GIT_SHA=<sha>`). Hand-posting this status defeats the only check that would have
  caught #478 before it shipped — don't.
- **Why it's a required branch-protection context:** so a release merge is *physically
  impossible* without a real bot run on a real image. That is the entire point.

## History this machinery answers for

v0.12.0 (2026-07-10) was released by hand and hit every gap this file closes: #478
shipped in rc.10 because no release check ran two bots; `:latest` sat a month stale
unnoticed; the service-image set nearly didn't ship with the lite image; the merge
stalled on an undocumented `release/vm-validated`; and no image carried a revision
label. Receipts: the CTO-space note `2026-07-10-release-machinery-inventory.md`.
