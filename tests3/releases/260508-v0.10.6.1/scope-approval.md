# Scope Approval — v0.10.6.1

Status: draft
Stage: `scope-sign`

This is the human signing surface. A machine can convert it to YAML later if
tooling needs that, but the human should sign Markdown.

## What I Reviewed

- [Scope design](scope-design.md) — why this release exists, what is in and
  out, and the design stance.
- [Scope document](scope.md) — the readable release contract.
- [Scope YAML](scope.yaml) — the machine release contract.
- [Scope verify](scope-verify.md) — audit/pushback and accepted decisions.

## My Summary

HUMAN WRITES:

> What am I approving, why now, and what trade-offs am I accepting?

## Attestations

- [ ] I read the scope more than once.
- [ ] I understand what this release does and why.
- [ ] I accept the trade-offs and believe this scope is deliverable.
- [ ] I authored or edited the final human-facing prose I am signing.

## Scope Items

- [ ] `finalizer-master-path-race`
  - Note:
- [ ] `pr-319-swagger-header-fix`
  - Note:
- [ ] `pr-239-camera-enabled-when-voice-agent`
  - Note:
- [ ] `pr-283-teams-continue-no-av-modal`
  - Note:
- [ ] `vexa-lite-docs-env-hygiene`
  - Note:
- [ ] `gmeet-rejection-fast-fail-and-rejoin`
  - Note:
- [ ] `callbacks-broad-except-narrow`
  - Note:
- [ ] `chunk-write-prior-count-cosmetic-log`
  - Note:
- [ ] `stale-issue-audit-sweep`
  - Note:
- [ ] `byo-tts-file-playback-validation`
  - Note:
- [ ] `tts-auto-language-detection`
  - Note:
- [ ] `recordings-playback-url-canonical`
  - Note:
- [ ] `drop-relational-recordings-tables`
  - Note:
- [ ] `migrations-convention-readme`
  - Note:
- [ ] `local-stack-walkability-smoke-gate`
  - Note:
- [ ] `local-human-browser-handoff-endpoints-ssot`
  - Note:
- [ ] `pre-release-security-dependency-floors`
  - Note:

## Explicit Deferrals

- [ ] [#289 dashboard/api-gateway 429](https://github.com/Vexa-ai/vexa/issues/289)
  — removed until re-triaged against current production behavior.
  - Note:
- [ ] [#303 audit-stage wiring](https://github.com/Vexa-ai/vexa/issues/303)
  — useful release-system work, but not part of this hotfix.
  - Note:

## Open Questions

Any open question blocks promotion to `develop-design`.

- Should any selected community PR defer after code review?
- Does any helm-only proof gap block release or become an explicit deferral?
- Should `release-sign` emit a standalone production handoff YAML?

## Promotion Decision

- [ ] Proceed to `develop-design`.

Signer:

- Name:
- Role:
- Signed at:
- Git SHA:

