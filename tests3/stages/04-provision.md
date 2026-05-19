# Stage: provision

| field        | value                                                       |
|--------------|-------------------------------------------------------------|
| Actor        | mechanical                                                  |
| Objective    | Stand up fresh infrastructure per `scope.deployments.modes`.|
| Inputs       | `scope.yaml`                                                |
| Outputs      | `tests3/.state-<mode>/*` populated; infra ready             |

## Steps (Makefile: `release-provision`)
1. `lib/stage.py assert-is develop` — must have completed develop first.
2. For each mode in `scope.deployments.modes`: run the mode's provision script in parallel.
   - `lite`    → `tests3 vm-provision-lite`
   - `compose` → `tests3 vm-provision-compose`
   - `helm`    → `tests3 lke-provision && tests3 lke-setup`
3. Wait for all to succeed.
4. `lib/stage.py enter provision`.

## Exit
All required state markers exist: `tests3/.state-<mode>/vm_ip` (or `lke_node_ip`).

## May NOT
- Run tests.
- Edit code.
- Skip failed infra ("works on existing VMs" is not allowed — fresh every cycle).

## Next
`deploy`.
