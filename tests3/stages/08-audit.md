# Stage: audit

> **Status: SCAFFOLD — landed in v0.10.5.3 release packet, full state-machine
> wiring (stage.py TRANSITIONS, Makefile `release-audit` target, skill
> `tests3/.claude/skills/audit/`) lands in v0.10.6.** This document defines
> the contract; v0.10.6 implements it.

| field        | value                                                                       |
|--------------|-----------------------------------------------------------------------------|
| Actor        | AI agent (static layer + contextual review) + human eyeroll on findings    |
| Objective    | Catch architectural / quality / security bad patterns that test-based validate cannot. |
| Inputs       | `git diff <last-shipped-tag>..HEAD` + `tests3/audit-categories.md` + `scope.yaml` |
| Outputs      | `releases/<id>/audit-findings.md` — table of findings (severity + file:line + recommendation) |

## Why this stage exists

`validate` is mechanical and binary: a test passes or fails. It cannot catch:
- A new fallback that was added without an explicit decision-record
- A complex workaround where a simple solution exists
- A security gap that no test covers (auth missing on a route, secret leaked, etc.)
- An unbounded buffer that doesn't crash today but will at month 9

These are caught by walking the cycle's diff against a checklist of known bad
patterns. The checklist starts narrow (what bit us in the last 3 cycles) and
grows by appending to `tests3/audit-categories.md` after each retro.

The cost: ~10–20 min per release. The benefit: catches the kind of regression
v0.10.5.2 shipped (chunk-buffer leak), v0.10.5.0 shipped (SDP-munge revert
incomplete), and v0.10.4 shipped (PII in OSS).

## Steps (Makefile: `release-audit` — landing in v0.10.6)

1. `lib/stage.py assert-is validate-green` — must be entered from `validate`
   on a green gate verdict (i.e. all DoDs passed). Audit runs AFTER validate
   so the test matrix has already exercised what it can.
2. **Compute scope** — `git diff $(last_shipped_tag)..HEAD` produces the
   delta this release is about to ship. Audit walks this delta only; legacy
   patterns in unchanged files are out of scope (file as separate cleanup
   issues).
3. **Static layer** — run `tests3/audit/patterns/*.sh` over the diff. Each
   pattern script is a focused grep+AST check: secrets in diff, `except: pass`
   without comment, `requests.get(...)` without `timeout=`, `cmd | tail`
   pipelines, `assert True` in tests, `print(` in service code, etc.
   Output: per-pattern findings with file:line + evidence string.
4. **Contextual layer** — invoke audit skill with the diff + the static-layer
   findings as context. Skill walks the categories that need judgment
   (workaround complexity, fallback deliberateness, architectural smell)
   and produces additional findings or upgrades static-layer severity based
   on context.
5. **Score** — assign severity to each finding per `audit-categories.md` rules.
6. **Verdict**:
   - **clean** (zero BLOCKER, zero CRITICAL) → `lib/stage.py enter human`
   - **issues** → `lib/stage.py enter triage` (or directly `develop` if the
     team prefers — see open question below). Findings list defines the
     fix-list for develop.
7. Write `releases/<id>/audit-findings.md` regardless of verdict — it's the
   audit trail.

## Output shape — `audit-findings.md`

```markdown
# Audit — <release-id>

**Verdict:** PASS | FAIL
**BLOCKER:** N · **CRITICAL:** N · **MAJOR:** N · **MINOR:** N

## Findings

| # | Severity | Category | File:line | Pattern | Recommendation |
|---|----------|----------|-----------|---------|----------------|
| 1 | CRITICAL | Security | services/api-gateway/main.py:2057 | New public callback proxy without auth or env gate | Wrap with _PACK_X_TEST_ROUTES_ENABLED check |
| ... | ... | ... | ... | ... | ... |

## What I checked but didn't find

- SQL injection — no f-string in execute() in new diffs
- Hardcoded secrets — none
- ...
```

## Severity calibration

- **BLOCKER** — security gap exposing user data, hardcoded secret in
  OSS-visible file, untracked critical fallback in customer-facing path.
  Stage MUST bounce back to develop. No "we'll file it for next" exit.
- **CRITICAL** — resilience anti-pattern in critical path that's bitten the
  team in the last 2 releases (e.g. unbounded buffer pattern, exit-code
  masking in CI). Stage bounces back to develop. Possible to argue for
  "ship + immediate followup" but only with explicit human decision-record.
- **MAJOR** — architectural smell, observability gap, weak test pattern.
  File `gh-issue` for next cycle, ship is fine.
- **MINOR** — hygiene. Track in `releases/<id>/audit-findings.md` only.

## Pre-positive contract

The first finding I run, AGAINST PRIOR PROD/TEST FINDINGS in the project's
last 5 retros, must be reported as PASS even if it surfaces things — the
prior-finding patterns are by definition pre-existing and out of audit
scope. Audit catches NEW regression introductions; cleanup of pre-existing
patterns is develop's job in dedicated-cleanup releases.

## May NOT

- Edit code (that's `develop`).
- Re-run tests (that's `validate`).
- Re-provision (that's `provision`).
- Mark a finding as fixed without a corresponding commit + re-validate cycle.
- Pad findings — a short honest report beats a long noisy one.
- Flag patterns that exist in legacy unchanged code and weren't touched
  in this cycle's diff.

## Exit

`audit-findings.md` exists AND `Verdict:` line says `PASS`. Then transition
`audit → human`. On `FAIL`, transition to `triage` (or `develop` per open
question below) and the develop loop owns clearing each finding.

## Open questions for v0.10.6 implementation

1. **`audit → develop` direct vs `audit → triage → develop`?** Triage exists
   for classification (regression vs gap). Audit findings are by definition
   regressions in this cycle's diff. Going direct to develop avoids a stage
   round-trip; routing through triage gives human a classification step.
   Recommend: `audit → develop` direct, since classification was implicit
   in the audit report's severity field.
2. **Static layer location** — `tests3/audit/patterns/*.sh` (new directory)
   or extend `tests3/tests/` with `audit-*.sh` prefix? Recommend: new
   directory — keeps audit checks operationally distinct from test checks.
3. **Skill location** — `tests3/.claude/skills/audit/SKILL.md` per the
   existing skill convention.
4. **Diff base ref** — `last-shipped-tag` is straightforward for normal
   cycles, but v0.10.5.x patch series chains might need `HEAD~N` heuristics.

## Next

`human` (on PASS) | `triage` or `develop` (on FAIL — open question above).

## AI operating context

You are in `audit`. Your objective: walk the cycle's diff against
`audit-categories.md`, produce a calibrated findings table, halt for human
on FAIL OR transition to human on PASS. You may NOT edit code, run tests,
or pad findings. Refuse pad: "If a category has zero findings in this diff,
say so explicitly; do not invent findings to fill quota." If a category
needs deep investigation, flag it as `needs-followup-audit` rather than
going down a rabbit hole.

Calibration: be CONSERVATIVE on BLOCKER/CRITICAL. Don't flag a pattern as
BLOCKER unless you can articulate the exact attack/failure mode. "Could
maybe be bad" → MAJOR or MINOR.
