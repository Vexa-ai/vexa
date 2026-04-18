# Stage: ship

| field        | value                                                               |
|--------------|---------------------------------------------------------------------|
| Actor        | mechanical                                                          |
| Objective    | Merge `dev → main`; promote `:dev → :latest` for every image.       |
| Inputs       | both Gate + human-approval green                                    |
| Outputs      | updated `main` branch, updated `:latest` tags on DockerHub          |

## Steps (Makefile: `release-ship`)
1. `lib/stage.py assert-is human`.
2. Re-verify: `release-human-gate` passes (all checklist items `[x]`); aggregator gate on the latest report is green.
3. Push `release/vm-validated` commit status on HEAD (required by branch protection).
4. Open PR dev → main (or reuse existing); merge.
5. Promote `:dev → :latest` on every image.
6. Fix `env-example` on main (IMAGE_TAG=latest — the `ENV_EXAMPLE_LATEST_ON_MAIN` lock).
7. `lib/stage.py enter ship`.

## Exit
`main` contains the merge commit; `:latest` tags updated; `release/vm-validated` status success on HEAD.

## May NOT
- Edit code.
- Skip either gate re-verification.
- Force-push main.
- Skip `env-example` fix (static lock will trip on next run).

## Next
`teardown`.
