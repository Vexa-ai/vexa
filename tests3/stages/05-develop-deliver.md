# Stage: develop-deliver

**Level:** dev · **Role:** do · **Inner-loop:** `do → audit → human → next`

| field        | value                                                                          |
|--------------|--------------------------------------------------------------------------------|
| Actor        | human (code) + AI (assist)                                                     |
| Objective    | Implement scope: code, tests3, docs. Deploy locally (LOCAL=1, lite+compose). Run LOCAL validate green. |
| Inputs       | `scope.yaml` (plan-approved)                                                   |
| Outputs      | commits on `dev` + LOCAL stack healthy + LOCAL validate report green           |

## Steps
1. `lib/stage.py assert-is develop-deliver`.
2. For each scope issue: implement `code_to_change`, `tests_to_add`, `docs_to_update`, register every new check id in `registry.yaml`.
3. Commit. Trailer: `release: <id> · stage: develop-deliver`.
4. `make release-deploy LOCAL=1 SCOPE=...` — builds locally, recreates lite + compose containers, curl-checks health ports.
5. `make release-validate LOCAL=1 SCOPE=...` — LOCAL prove run.
6. Inner-loop: `bash tests3/lib/hot-iterate.sh <service>` for one-line fixes.

## Exit
- All scope issues have commits.
- Every new prove id is in `registry.yaml`.
- LOCAL=1 lite + compose healthy.
- **`bash tests3/tests/walkability-smoke.sh --mode compose` AND `--mode lite` both green (six proves each)** — this is the bridge between "containers up" and "human can walk." Refuses develop-verify transition if either red.
- **`make -C tests3 scope-proof-gate-local SCOPE=tests3/releases/<id>/scope.yaml` green** — every scope-bound prove runnable on LOCAL=1 (`lite` / `compose`) is actually present and `pass` in the latest LOCAL reports. This closes the gap between "the matrix ran" and "the release's required proof cells are green."
- LOCAL validate green on every {check, mode ∈ {lite, compose}} pair.

## May NOT
- Touch helm or canonical DockerHub push (that's `stage-deliver`).
- Mark any approval `true`.
- Add a fallback without a corresponding `explicit_decisions:` entry in scope.yaml.
- Leak customer PII into release artefacts.

## Next
`develop-verify`.

## AI operating context
You implement code/tests/docs per `scope.yaml`. You drive LOCAL=1 deploy + LOCAL validate. You do NOT advance to develop-verify until LOCAL validate is green.
