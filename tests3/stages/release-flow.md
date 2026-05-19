# Release Flow — Environment Promotion Model

Redesigned 2026-05-15.

The release system is an environment promotion flow. Each environment breathes
through the same four-role inner loop:

```text
design -> deliver -> verify -> sign
```

This repo owns public release readiness:

```text
scope -> develop -> stage -> release -> done
```

Production promotion is downstream and belongs to the production/operations
system. This repo ends by producing a signed public release and a production
handoff.

## Linear Path

```text
scope-design
  -> scope-deliver
  -> scope-verify
  -> scope-sign
  -> develop-design
  -> develop-deliver
  -> develop-verify
  -> develop-sign
  -> stage-design
  -> stage-deliver
  -> stage-verify
  -> stage-sign
  -> release-design
  -> release-deliver
  -> release-verify
  -> release-sign
  -> done
```

## Meaning

| Level | Environment | Reads | Produces |
|---|---|---|---|
| `scope` | planning environment | issues, production logs, emails, customer/support signals, strategy | release intent and signed scope |
| `develop` | local environment | signed scope | implementation, docs, tests, local deploy, local validation |
| `stage` | throwaway production-like infra | signed local artifact | canonical validation on disposable infra |
| `release` | public distribution | signed staged artifact | GitHub/DockerHub/package release, release notes, production handoff |

## Role Semantics

| Role | Meaning |
|---|---|
| `design` | Decide what this level must produce and what constraints matter here. |
| `deliver` | Materialize the artifact for this level. |
| `verify` | Run machine checks, audits, consistency gates, and hard feedback. |
| `sign` | Human judgment/authorship/acceptance and the promotion decision. |

The human is involved in `design` and again at `sign`. Verification is mostly
machine-owned, but its result feeds the human signoff.

## Feedback Edges

Hard feedback does not create a separate triage stage. It bounces to the
nearest useful role:

```text
<level>-verify -> <level>-deliver
<level>-sign   -> <level>-deliver
```

If the failure proves the intent is wrong rather than the materialization, the
operator bounces to:

```text
<level>-verify -> <level>-design
<level>-sign   -> <level>-design
```

Examples:

- `develop-verify -> develop-deliver`: diff has an unagreed fallback.
- `develop-sign -> develop-design`: human rejects the product approach, not
  just the implementation.
- `stage-verify -> stage-deliver`: canonical artifact or validate report is
  incomplete.
- `stage-sign -> stage-design`: the stage environment shape was wrong.

## Current Migration Map

| Old Stage | Canonical Stage |
|---|---|
| `groom` | `scope-design` |
| `plan-solution` | `scope-deliver` |
| `plan-audit` | `scope-verify` |
| `plan-human` | `scope-sign` |
| `develop-code` | `develop-deliver` |
| `develop-audit` | `develop-verify` |
| `develop-human` | `develop-sign` |
| `stage` | `stage-deliver` |
| `stage-audit` | `stage-verify` |
| `stage-human` | `stage-sign` |
| `release` | `release-deliver` + `release-verify` + `release-sign` |
| `teardown` | `done` |

## Production Boundary

`release` is not `production`.

`release` means the public OSS/distribution artifact exists and matches what
was approved.

`production` means Vexa's hosted/customer-facing system safely runs that
artifact. Production rollout uses the signed handoff emitted by this repo, but
is not a tests3 state.

