# Stage: develop-verify

**Level:** dev · **Role:** audit · **Inner-loop:** `do → audit → human → next`

| field        | value                                                              |
|--------------|--------------------------------------------------------------------|
| Actor        | AI agent + human eyeroll on findings                               |
| Objective    | Apply the 4-check audit rubric to the *actual diff*.               |
| Inputs       | `git diff <last-shipped-tag>..HEAD` + `scope.yaml` + `tests3/audit-categories.md` + Hardenloop tool/repo if available |
| Outputs      | `releases/<id>/develop-verify-findings.md` (must open with CTO briefing block per `tests3/communication-standard.md`) |

## What develop-verify inspects (7-principle rubric, applied to the diff)

Canonical rubric in `tests3/audit-categories.md`. Applied to
`git diff <last-shipped-tag>..HEAD`:

1. **Justification** — does the diff match the scope's `justification:`? Any unjustified edit (commit that doesn't trace back to a scope issue) is a finding.
2. **Blast radius + mitigation** — does the implementation match the `blast_radius:` declared at plan? If the diff turns out to be bigger than scope predicted (e.g. touches more services), the blast-radius answer must be re-validated. Missing rollback path = BLOCKER.
3. **API backwards compatibility** — diff scan for: renamed REST routes, removed env vars, changed webhook payloads, altered CLI flags, changed docker entry points, changed registry check IDs, broken Python/TS package signatures. Any unagreed break = BLOCKER.
4. **No DB migrations** — diff scan for `ALTER TABLE`, alembic `op.*`, new SQLAlchemy columns, dropped indexes. Any schema change without a `migration_decision:` block = BLOCKER.
5. **Fail fast — no fallbacks** — diff scan for new `try / except`, `if not X: return default`, `or fallback_value`, "buffer kept just in case" patterns. Each new occurrence must match a scope `explicit_decisions:` entry + a `#NNN` source-line ref. Unagreed = BLOCKER.
6. **Security** — diff for new env-secret reads, auth-decorator removals, PII fields hitting `releases/<id>/`, unbounded buffers.
7. **Industry best practice** — idempotency on new handlers, structured errors, log/metric coverage, naming, dependency hygiene, no copy-paste where a helper exists.

## Steps
1. `lib/stage.py assert-is develop-verify`.
2. Compute scope diff. Run `tests3/audit/patterns/*.sh` over the diff. Walk the rubric.
3. Run Hardenloop audit when available and save its report/receipt under `releases/<id>/`. If Hardenloop is unavailable, include an explicit `hardenloop: unavailable` note with the reason (for example repo inaccessible or tool not installed); this is not a silent pass.
4. Write `develop-verify-findings.md` (severity / file:line / finding / recommendation).
5. For each HIGH/CRITICAL from either tests3 audit or Hardenloop: either bounce to `develop-deliver` for fix, or amend scope.yaml `explicit_decisions:` for explicit override.

## Exit
No HIGH/CRITICAL findings left unresolved, and Hardenloop status is recorded.

## May NOT
- Edit code or scope (bounces are reverse-edge transitions).
- Approve anything (`develop-sign` owns approval).

## Next
`develop-sign` — on clean / explicitly-decided findings.
`develop-deliver` — on findings that need code change.
