# Stage: done

**Level:** — · **Role:** cleanup

| field        | value                                                      |
|--------------|------------------------------------------------------------|
| Actor        | mechanical                                                 |
| Objective    | Destroy throwaway infra; archive release artefacts.        |
| Inputs       | signed public release + `scope.yaml` + `tests3/.state-<mode>/*` |
| Outputs      | clean `.state/`; no residual VMs / clusters                |

## Steps (Makefile: `release-done`)
1. `lib/stage.py assert-is done`.
2. For each mode in scope: destroy infra (`vm-destroy` / `lke-destroy`).
3. Archive `releases/<id>/` (keep on disk; never delete).
4. Leave `.current-stage` at `done` until the next release starts at
   `scope-design`.

## Exit
No throwaway VMs / clusters running; `.current-stage → done`.

## May NOT
- Run against a `release_id` mismatch (would destroy wrong infra).
- Delete `releases/<id>/` (archive only).
- Skip any mode's destroy step.

## Next
`scope-design`.
