# Stage: ship

| field        | value                                                               |
|--------------|---------------------------------------------------------------------|
| Actor        | mechanical                                                          |
| Objective    | Merge `dev → main`; tag the release; publish images under stable, version-named tags; promote `:latest`. |
| Inputs       | both Gate + human-approval green                                    |
| Outputs      | updated `main` branch · `vX.Y.Z` git tag · `vexaai/<svc>:vX.Y.Z` images on DockerHub · updated `:latest` tags · matching helm chart release |

## Steps (Makefile: `release-ship`)

1. `lib/stage.py assert-is human`.
2. Re-verify: `release-human-gate` passes (all checklist items `[x]`); aggregator gate on the latest report is green.
3. Push `release/vm-validated` commit status on HEAD (required by branch protection).
4. Open PR dev → main (or reuse existing); merge.
5. **Bump chart `version` AND `appVersion`** in `deploy/helm/charts/vexa/Chart.yaml` so chart-release CI publishes a new pkg with this release. Patch releases (vX.Y.Z.W) bump only `appVersion`; chart-release CI must have `skip_existing: true` for that to be a no-op (see Hygiene §3).
6. **Tag the merge commit** with `git tag -a vX.Y.Z -m "<release notes>"` and `git push origin vX.Y.Z`.
7. **Publish images under the version-named tag — not just `:dev`**: for every image declared in `deploy/compose/Makefile $(IMAGES)`, run:
   ```
   docker tag vexaai/<svc>:dev vexaai/<svc>:vX.Y.Z
   docker push vexaai/<svc>:vX.Y.Z
   ```
   Then promote: `docker tag vexaai/<svc>:vX.Y.Z vexaai/<svc>:latest && docker push vexaai/<svc>:latest`.
8. Fix `env-example` on main (IMAGE_TAG=latest — the `ENV_EXAMPLE_LATEST_ON_MAIN` lock).
9. `lib/stage.py enter ship`.

## Release hygiene — invariants enforced at this stage

These are MANDATORY. Skipping any of them breaks reproducibility and downstream trust in image tags. Each was added after a real-world incident; do not relax without a documented reason.

### 1. Image-as-artifact: build ONCE, before gate, never rebuild after green

Once the validate matrix passes green, the binary images on DockerHub MUST be the binary images deployed to prod. Any rebuild between gate-green and ship produces a different digest — even with identical source — because of layer ordering, build-arg fingerprinting, base-image drift. **If you need to fix something after green, that is a new release cycle.**

Concrete rule: the digest of `vexaai/<svc>:vX.Y.Z` on DockerHub MUST match the digest the gate matrix consumed. Verify with `docker manifest inspect vexaai/<svc>:vX.Y.Z` and compare against the digest recorded in the gate's report file (or in `tests3/.state-*/reports/<mode>/*.json`).

### 2. Tag every published image with its release version

Never deploy from `:dev` or a date-stamped intermediate tag. Always re-tag and push `vexaai/<svc>:vX.Y.Z` BEFORE updating any consumer that pins by version.

`make promote-latest` retags whatever is at `:vX.Y.Z` to `:latest`. **If `:vX.Y.Z` is wrong (e.g. you only pushed `:dev` and never `:vX.Y.Z`), promoting `:latest` will silently propagate the wrong content.** Always push `:vX.Y.Z` first, manually verify the digest, then promote.

### 3. Patch releases (vX.Y.Z.W) follow the same rules at smaller scope

A bug found post-ship that requires a small code fix is a NEW release, not an in-place rebuild. Do:

- Open PR with the patch on the release branch
- Merge, tag `vX.Y.Z.1` (or `vX.Y.Z.N` for subsequent)
- Build + push images under `vexaai/<svc>:vX.Y.Z.1`
- Bump `Chart.yaml.appVersion` (chart `version` stays unchanged unless chart pkg shape changed)
- Bump platform consumers (vexa-platform `chart/vexa-platform/values-base.yaml` etc) to the new version-named image tag
- Re-deploy

DO NOT re-tag images that previously meant something else. The tag `vexaai/<svc>:vX.Y.Z` is immutable after ship — only `:latest` (and `:staging` if used) point at evolving content.

### 4. Image-build CI on tag push (TODO)

Currently, only manual `make build` + `make publish` produces images. There is no GitHub Actions workflow that builds + pushes images on tag push or merge. This is a release-mechanics gap; until it lands, the human running ship MUST run `make publish` explicitly before `release-ship` exits, and verify the published `:vX.Y.Z` digests match.

When the workflow lands, this section becomes:
- Push `vX.Y.Z` tag
- CI builds + pushes `vexaai/<svc>:vX.Y.Z` for every service in `$(IMAGES)`
- Manual step in this stage becomes: verify CI succeeded, do not deploy until then.

### 5. Helm chart-release CI must be idempotent

`deploy/helm/charts/vexa/Chart.yaml` is consumed by the `chart-release` GitHub Actions workflow (chart-releaser-action) which packages and publishes to `gh-pages` + a GH release. The action must be configured with `skip_existing: true` so an `appVersion`-only bump (without `version` change) is a clean no-op rather than a 422 "tag already exists" failure. See `.github/workflows/chart-release.yml`.

## Exit

- `main` contains the merge commit
- `vX.Y.Z` git tag exists on origin
- `vexaai/<svc>:vX.Y.Z` and `:latest` exist on DockerHub for every service in `$(IMAGES)`, with matching digests
- `release/vm-validated` status success on HEAD

## May NOT

- Edit code (any source change is a new release; see Hygiene §3).
- Skip either gate re-verification.
- Force-push main.
- Skip `env-example` fix (static lock will trip on next run).
- **Push images only as `:dev`** (Hygiene §2 — you must tag and push `:vX.Y.Z` explicitly).
- **Rebuild any image between gate-green and ship** (Hygiene §1).
- **Re-tag a previously-shipped `:vX.Y.Z`** to point at different content (Hygiene §3 — that tag is immutable post-ship).

## Next

`teardown`.
