# Stage: release-deliver

**Level:** release · **Role:** deliver · **Inner-loop:** `design → deliver → verify → sign`

| field        | value                                                                       |
|--------------|-----------------------------------------------------------------------------|
| Actor        | mechanical                                                                  |
| Objective    | Deliver the software: merge `dev → main`, tag images, publish `:dev → :latest`, write release notes. |
| Inputs       | `release-design` complete + `stage-sign` green: validate matrix green + `human-approval.yaml` both parts signed |
| Outputs      | updated `main` branch + `:latest` tags on DockerHub + `RELEASE_NOTES.md` updated + git tag `vX.Y.Z` |

## Production validation

Production validation uses the production variant in
[human-validation-harness.md](../human-validation-harness.md). It is a
post-ship or canary confirmation with the smallest safe blast radius; it is not
a substitute for `stage-sign`.

Rules:

- use a test account or explicitly approved customer-safe canary;
- machine dispatches/probes and presents the exact artifact verdict;
- human only admits/listens/judges where human senses are required;
- no broad customer-affecting probes, billing mutations, sends, or deletes;
- any customer-risk symptom triggers rollback/hotfix/do-not-release handling.

## Steps (Makefile: `release-publish`)

1. `lib/stage.py assert-is release-deliver`.
2. Re-verify: latest validate-report green; both human-approval parts true.
3. Bump `VERSION` file to the release id's version prefix (e.g. `0.10.6.1`); commit.
4. Push `release/vm-validated` commit status on HEAD (required by branch protection).
5. Open or reuse PR `dev → main`; merge.
6. Tag the merge commit: `git tag vX.Y.Z` + push tag.
7. Promote `:dev → :latest` on every image on DockerHub.
8. Fix `env-example` on main (`IMAGE_TAG=latest` — `ENV_EXAMPLE_LATEST_ON_MAIN` lock).
9. Generate `RELEASE_NOTES.md` from scope + commit log; commit on main.
10. Continue to `release-verify`.

## Exit
- `main` contains the merge commit + version bump + release notes.
- Git tag `vX.Y.Z` exists on the merge commit.
- `:latest` tags updated on DockerHub for every image.
- `release/vm-validated` status success on HEAD.
- `scope.md` is renamed/moved to `RELEASE_NOTES.md` on `main` (or
  mirrored — release-notes are the public face of the same authored
  doc that travelled the cycle).
- A draft production handoff exists and points at public release artifacts.

## May NOT
- Edit code.
- Skip gate re-verification.
- Force-push main.
- Skip `env-example` fix (static lock trips next run).
- Skip image-tag promotion (customers pulling `:latest` get stale code).
- Skip the `vX.Y.Z` git tag (release-notes + rollback rely on it).

## Next
`release-verify`.
