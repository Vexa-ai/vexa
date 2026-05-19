# Scope Verify — <release_id>

Status: draft
Stage: `scope-verify`

Purpose: push back on the proposed scope before code exists.

Inputs:

- `releases/<id>/scope-design.md`
- `releases/<id>/scope.md`
- `releases/<id>/scope.yaml`
- `tests3/audit-categories.md`
- linked issues/logs/customer signals named by the scope artifacts

## Verdict

Verdict: `blocked | accepted-with-decisions | green`

One-paragraph summary:

<Say what is safe to proceed with, what must change, and what is being
explicitly accepted.>

## Checks

| Principle | Verdict | Notes |
|---|---:|---|
| Justification | `<pass/fail>` | <Does each item explain what problem, why this approach, alternatives, and why now?> |
| Blast radius + mitigation | `<pass/fail>` | <Who is affected, detection signal, rollback path, slow-rollback mitigation?> |
| API compatibility | `<pass/fail>` | <Every public surface declared and compatible or deprecation-decisioned?> |
| DB migrations | `<pass/fail>` | <None, or complete migration decision?> |
| Fail fast / no unagreed fallbacks | `<pass/fail>` | <Any fallback/workaround must have explicit decision and proof.> |
| Security/privacy | `<pass/fail>` | <Auth, secrets, PII, unbounded resources, OWASP-10.> |
| Best practice | `<pass/fail>` | <Idempotency, error semantics, observability, naming, dependency hygiene.> |

## Findings

| Severity | Scope item | Finding | Required action |
|---|---|---|---|
| `<BLOCKER/HIGH/MEDIUM/LOW>` | `<id>` | <specific finding with link/path if relevant> | <fix scope, add decision, defer, or accept> |

## Explicit Decisions Accepted

Only list decisions accepted during verify. These must also be reflected in
`scope.yaml:explicit_decisions`.

| Decision | Scope reference | Why acceptable |
|---|---|---|
| <decision> | <scope item / line / link> | <reason> |

## Required Bounce

If verdict is blocked, name the target stage and exact work:

```text
bounce: scope-deliver
reason: <what must change>
```

## Exit

`scope-verify` is complete when there are no unresolved HIGH/CRITICAL findings
and every accepted exception is recorded in the scope contract.

