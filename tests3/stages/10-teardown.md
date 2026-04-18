# Stage: teardown

| field        | value                                                      |
|--------------|------------------------------------------------------------|
| Actor        | mechanical                                                 |
| Objective    | Destroy provisioned infra.                                 |
| Inputs       | `scope.yaml` + `tests3/.state-<mode>/*`                    |
| Outputs      | clean `.state/`; no residual VMs / clusters                |

## Steps (Makefile: `release-teardown`)
1. `lib/stage.py assert-is ship`.
2. For each mode in scope: destroy infra (`vm-destroy` / `lke-destroy`).
3. Archive `releases/<id>/` (keep; never delete).
4. `lib/stage.py enter teardown`.
5. Immediately: `lib/stage.py enter idle` to close the cycle.

## Exit
No VMs / clusters running under `tests3/.state-*/`; `.current-stage` → `idle`.

## May NOT
- Run against a `release_id` mismatch (destroys wrong infra).
- Skip any mode's destroy step.

## Next
`idle`.
