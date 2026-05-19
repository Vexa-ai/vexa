# Stage: stage-verify

**Level:** stage · **Role:** audit · **Inner-loop:** `do → audit → human → next`

| field        | value                                                              |
|--------------|--------------------------------------------------------------------|
| Actor        | AI agent + human eyeroll on findings                               |
| Objective    | Apply the 4-check audit rubric to *canonical deployment artefacts*. |
| Inputs       | rendered helm manifests + `deploy/compose/docker-compose.yml` (resolved IMAGE_TAG) + `deploy/lite/*` + `validate-report-<ts>.md` + `scope.yaml` |
| Outputs      | `releases/<id>/stage-verify-findings.md` (must open with CTO briefing block per `tests3/communication-standard.md`) |

## What stage-verify inspects (7-principle rubric, applied to canonical artefacts)

Canonical rubric in `tests3/audit-categories.md`. Applied to rendered
helm manifests + resolved compose + lite deploy scripts + the validate
report:

1. **Justification** — does each non-default config setting (image tags, probe delays, resource limits, env vars) trace to a scope item or a baseline default? Unjustified config drift = finding.
2. **Blast radius + mitigation** — does the canonical config preserve the rollback path scope declared? E.g. did a probe-delay tweak silently lock us into a deploy that can't be rolled back without a downtime window? Helm-chart `--atomic` / rollback hooks present? Verified.
3. **API backwards compatibility** — env-var names + defaults in `deploy/env-example` unchanged on customer-facing fields. Image entry points unchanged. Helm chart `appVersion` bump consistent with published docs.
4. **No DB migrations** — canonical deploy must NOT auto-run migrations on customer infra unless `scope.yaml:migration_decision:` says so AND the deploy step is gated behind a flag.
5. **Fail fast — no fallbacks** — infra-level fallbacks: env-example fallbacks (`OPENAI_API_KEY=sk-changeme`), default-namespace fallbacks, "if MINIO_ENDPOINT unset use localhost", probe-delay tweaks that mask a slow-boot bug rather than fix it. Each occurrence needs an explicit decision.
6. **Security** — secrets in env (no plaintext, no default `change-me`), exposed ports, RBAC on helm resources, TLS, image provenance (signed `:dev` tag), no debug endpoints reachable in prod manifests.
7. **Industry best practice** — image tag immutability (no `:latest` in helm), resource limits + requests set, readiness/liveness probes for every container, structured-log routing, metrics endpoint exposed, no dev-only flags leaking into prod manifests.

## Steps
1. `lib/stage.py assert-is stage-verify`.
2. AI walks the canonical artefacts + the validate report against the 4-check rubric. Writes `stage-verify-findings.md`.
3. For each HIGH/CRITICAL: bounce to `develop-deliver` (canonical-config edits live in `deploy/**` and ship via dev branch).

## Exit
No HIGH/CRITICAL findings left unresolved.

## May NOT
- Edit code or canonical configs (bounces to `develop-deliver`).
- Approve any human gate.

## Next
`stage-sign` — on clean.
`develop-deliver` — on rejection.
