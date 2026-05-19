# Scope Verify — v0.10.6.1

Status: express canonicalization
Stage: `scope-verify`

Purpose: push back on the proposed scope before code exists, using the new
canonical artifact name. This file supersedes the older compatibility artifact
`plan-audit-findings.md` for the scope level.

Inputs:

- `releases/260508-v0.10.6.1/scope-design.md`
- `releases/260508-v0.10.6.1/scope.md`
- `releases/260508-v0.10.6.1/scope.yaml`
- `releases/260508-v0.10.6.1/plan-audit-findings.md`
- `tests3/audit-categories.md`

## Verdict

Verdict: `accepted-with-decisions`

The scope contract is coherent enough to proceed to human signing. The older
plan-audit pass is carried forward under the new `scope-verify` name. The
release has explicit trade-offs, ADRs, release-level principle compliance,
scope-bound proof intent, and human-checklist intent. The only historical
minor finding was the need to treat the multichunk backfill as migration-class
work; the current scope now carries explicit migration/rollback material for
the recording-storage work and the release keeps that risk visible.

## Checks

| Principle | Verdict | Notes |
|---|---:|---|
| Justification | `pass` | Release-level thesis and per-issue rationale are present in `scope.md` and `scope.yaml`. |
| Blast radius + mitigation | `pass` | Release-level rollback and per-row blast-radius language are present; helm rehearsal cost is explicitly accepted. |
| API compatibility | `pass` | `playback_url` is additive; `media_files[]` receives a one-release deprecation window. |
| DB migrations | `pass-with-decision` | Recording-storage cleanup is migration-class and has explicit restore/rollback material. Backfill/destructive migration risk remains visible to later stages. |
| Fail fast / no unagreed fallbacks | `pass` | Scope intent removes known fallback paths rather than adding new silent defaults. |
| Security/privacy | `pass` | Security audit handling is referenced without exposing sensitive advisory details in public artifacts. |
| Best practice | `pass` | Scope names idempotency, canonical playback ownership, migration convention documentation, and dependency hygiene. |

## Findings

| Severity | Scope item | Finding | Required action |
|---|---|---|---|
| LOW | Scope artifact names | Older compatibility names still exist (`plan-audit-findings.md`, `plan-approval.yaml`) beside canonical names. | Keep compatibility for this release; use canonical names for new artifacts and later tooling. |
| LOW | Stage terminology | Some existing release docs still mention old stage names such as `plan-human` or `develop-code`. | Do not block v0.10.6.1; continue canonicalizing stage docs/tooling while walking the release. |

## Explicit Decisions Accepted

| Decision | Scope reference | Why acceptable |
|---|---|---|
| Keep v0.10.6.1 focused on hotfix + low-hanging fruit | `scope-design.md`, `scope.md` | Reduces cognitive load and keeps customer-impacting regressions central. |
| Exclude [#289](https://github.com/Vexa-ai/vexa/issues/289) until re-triaged | `scope-design.md` | The issue framing no longer matches current production behavior; shipping against stale symptoms would waste release attention. |
| Exclude [#303](https://github.com/Vexa-ai/vexa/issues/303) audit-stage wiring | `scope-design.md` | Valuable release-system work, but it deserves its own wiring cycle rather than diluting this hotfix. |
| Keep release and production separate | `scope-design.md`, `stages/release-flow.md` | This repo owns public release readiness; production rollout belongs to the downstream production/operations system. |

## Required Bounce

None for express path.

```text
bounce: none
reason: no unresolved HIGH/CRITICAL scope findings
```

## Exit

`scope-verify` is complete when the human can review this pushback and decide
whether the scope contract is ready for `scope-sign`.

