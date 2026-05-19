# Stage: stage-deliver

**Level:** stage · **Role:** do · **Inner-loop:** `do → audit → human → next`

| field        | value                                                                       |
|--------------|-----------------------------------------------------------------------------|
| Actor        | mechanical                                                                  |
| Objective    | Canonical authoritative run: provision throwaway infra, deploy `:dev` images via DockerHub, run full validate matrix on all required modes. |
| Inputs       | `dev` HEAD (green from develop-sign)                                       |
| Outputs      | `releases/<id>/validate-report-<ts>.md`                                     |

## Substeps (mechanical; `make release-stage` orchestrates)

1. **Provision** — fresh throwaway for every required mode (`lite` VM, `compose` VM, `helm` LKE namespace).
2. **Deploy (canonical)** — `make release-build` publishes `:dev` to DockerHub; each mode pulls + starts. NOT `LOCAL=1`.
3. **Validate** — `make release-validate SCOPE=...` runs the full matrix across all required modes. Writes `validate-report-<ts>.md`.

## Exit
Validate matrix green on every required `{check, mode}` pair (modulo formally-deferred items in `scope.yaml:deferred[]`).

## May NOT
- `LOCAL=1` (that's the develop loop).
- Edit code or scope (red bounces all the way to `develop-deliver`).
- Approve any human gate.
- Promote `:dev → :latest` (that's `release-deliver`).

## Next
`stage-verify` — on green.
`develop-deliver` — on red (the verdict report carries forward as the new triage-log).
