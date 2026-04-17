---
name: 8-ship
description: "Invoke when the user wants to ship a Vexa release — merge dev→main and promote :dev images to :latest on DockerHub. Runs ONLY after stage 6 (automated gate) and stage 7 (human gate) are both green. Use when the user says 'ship it', 'merge to main', 'promote to latest', 'release it', 'publish the release', or after both gates are signed off."
---

## Stage 8 of 9 — ship

Both gates are green. This is the merge.

## Command

```bash
make release-ship SCOPE=$SCOPE
```

## What it does

1. **Human gate re-check**: runs `release-human-gate` — refuses to proceed if any `- [ ]` remains in `tests3/releases/<id>/human-checklist.md`.
2. **Push GitHub commit status** `release/vm-validated` on HEAD (required by branch protection on `main`).
3. **Create or merge the PR** `dev → main` (via `gh pr create` or `gh pr merge`).
4. **Fix env-example on main**: after merge, checks out main, rewrites `IMAGE_TAG=dev → IMAGE_TAG=latest` in `deploy/env-example` (and BROWSER_IMAGE similarly), commits + pushes. This is the fix for the well-known "IMAGE_TAG=dev slipped into main" lock.
5. **Promote images** `:dev → :latest` on DockerHub via `make -C deploy/compose promote-latest`. Every vexa image (api-gateway, admin-api, meeting-api, runtime-api, agent-api, mcp, dashboard, tts-service, vexa-bot, vexa-lite) gets a `:latest` tag pointing at the same digest as the `:dev` tag that just passed validation.

## Prerequisites (all enforced or verified)

- `SCOPE` is set and the scope file exists.
- Stage 6 passed (`tests3/reports/release-<tag>.md` exists and shows "Release gate PASSED").
- Stage 7 passed (human checklist has no `- [ ]` left).
- You are on `dev` branch, committed, and pushed.
- DockerHub auth configured (same credentials used at stage 4).
- `gh` authenticated with write access to `Vexa-ai/vexa`.

## What does NOT happen here

- **Teardown is a separate stage.** `release-ship` does NOT destroy the VMs / LKE cluster — that's stage 9 (`release-teardown`). The rationale: if something goes wrong during promotion, you still have the validated deployments as a known-good reference.
- **No retroactive rewrites.** If the gate that should have failed didn't, and you realize after ship, revert the merge and start a new release cycle. Don't patch main in place.

## Verifying the ship

```bash
# Release commit status
SHA=$(git rev-parse origin/main)
gh api "repos/Vexa-ai/vexa/commits/$SHA/statuses" --jq '.[] | select(.context=="release/vm-validated") | .state'

# :latest digests match the built :dev
for image in api-gateway admin-api meeting-api runtime-api dashboard vexa-bot; do
    DEV_DIGEST=$(docker buildx imagetools inspect vexaai/$image:dev --format '{{.Manifest.Digest}}')
    LATEST_DIGEST=$(docker buildx imagetools inspect vexaai/$image:latest --format '{{.Manifest.Digest}}')
    [ "$DEV_DIGEST" = "$LATEST_DIGEST" ] && echo "  $image: ✓ $DEV_DIGEST" || echo "  $image: ✗ dev=$DEV_DIGEST latest=$LATEST_DIGEST"
done
```

## After ship

- Close every GH issue referenced in the scope: `gh issue close <N> --comment "Shipped in release <tag>"`.
- Post to Discord #announcements with a short summary of what changed.
- Run stage 9 (`9-teardown`) to destroy the VMs / LKE cluster.

## If ship fails midway

`release-ship` is not transactional — if it fails after the merge but before promotion, you're in a partial state. Recover:

- **Merge happened, promotion didn't**: re-run `make -C deploy/compose promote-latest`.
- **env-example fix didn't land on main**: re-apply manually, commit + push to main directly.
- **GH status failed to post**: `make release-validate` (or run the `gh api` call from the Makefile by hand).

## Next

→ stage 9: `make release-teardown SCOPE=$SCOPE` — destroy the VMs + LKE cluster.
