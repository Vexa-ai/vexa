# Stage: scope-verify

**Level:** scope · **Role:** verify · **Inner-loop:** `design → deliver → verify → sign`

| field        | value                                                              |
|--------------|--------------------------------------------------------------------|
| Actor        | AI agent + human eyeroll on findings                               |
| Objective    | Audit the *proposed* solution against the 4-check rubric.          |
| Inputs       | `releases/<id>/scope-design.md` + `releases/<id>/scope.md` + `releases/<id>/scope.yaml` + `tests3/audit-categories.md` |
| Outputs      | `releases/<id>/scope-verify.md`, using `tests3/templates/scope/scope-verify.md`. |

## Audit rubric (7 principles)

Canonical rubric lives in `tests3/audit-categories.md`. The seven
principles every audit (scope-verify, develop-verify, stage-verify) MUST
answer:

1. **Justification** — what problem, why this approach, ≥2 alternatives + why rejected, why cleanest *now*.
2. **Blast radius + mitigation** — who/what affected, severity, detection signal, rollback path, slow-rollback mitigation. Missing answer = BLOCKER.
3. **API backwards compatibility** — public surfaces unchanged or deprecation-decisioned.
4. **No DB migrations** unless explicitly decisioned.
5. **Fail fast — no fallbacks** unless explicitly agreed.
6. **Security** — auth, secrets, PII, unbounded resources, OWASP-10.
7. **Industry best practice** — idempotency, error semantics, observability, naming.

## What scope-verify specifically inspects

The *proposal*, not yet code. For each scope-issue:
- Does `justification:` answer all four sub-questions concretely?
- Does `blast_radius:` cover all five fields? (who_affected / severity_if_wrong / detection_signal / rollback_path / mitigation_if_rollback_slow)
- Does `api_compat:` declare every public-surface change?
- Is `migration_decision:` either `none` or a complete migration block?
- Each `hypothesis:` — root cause or symptom-patch?
- Each `code_to_change:` edit — does it propose a new fallback path?
- Each `tests_to_add:` — is there a fail-fast assertion?
- `registry_changes` — naming, weight inflation, reused-check-id collisions.

## Steps
1. `lib/stage.py assert-is scope-verify`.
2. AI walks scope artifacts against the rubric. Writes `scope-verify.md`
   (verdict / principle checks / findings / explicit decisions / bounce).
3. For each finding: either accept (bounce back to `scope-deliver` for amendment) or record an `explicit_decisions:` override in `scope.yaml`.

## Exit
No HIGH/CRITICAL findings left unresolved.

## May NOT
- Edit code or scope.yaml (bounces are explicit reverse-edge transitions).
- Approve anything (scope-sign owns approval).

## Next
`scope-sign` — on clean / explicitly-decided findings.
`scope-deliver` — on findings that require scope amendment.
