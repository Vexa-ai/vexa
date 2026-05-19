# Stage: deploy

| field        | value                                                   |
|--------------|---------------------------------------------------------|
| Actor        | mechanical                                              |
| Objective    | Build + push `:dev` images; pull on all provisioned modes. |
| Inputs       | current `dev` HEAD + provisioned infra                  |
| Outputs      | every deployment running the current `image_tag`        |

## Steps (Makefile: `release-deploy`)
1. `lib/stage.py assert-is provision|develop` — deploy may be re-entered from develop after a triage-driven fix.
2. `make release-build` → publish `:dev` + record `deploy/compose/.last-tag`.
3. For each mode in scope: pull + restart.
4. `lib/stage.py enter deploy`.

## Exit
All services running the newly-published tag on every mode.

## May NOT
- Edit code.
- Run tests.

## Next
`validate`.
