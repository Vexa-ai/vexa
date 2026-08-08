# ADR-0034 — Contributor rights are declared at intake and corporate evidence is head-bound

**Status:** proposed

## Context

Apache-2.0 supplies inbound contribution terms, but a pull request can still leave ambiguity about
whether an individual or an employer controls the submitted work. Requiring every individual to
sign a CLA would add disproportionate friction. A generic maintainer label is not sufficient
corporate evidence because it can survive later pushes and does not identify a private receipt.

## Decision

New pull requests choose exactly one of three paths: independent, employer/client-controlled, or
unsure. Independent contributors use DCO 1.1 per commit and no individual CLA. Corporate work uses
the same individual DCO plus a private corporate agreement or narrow authorization. The public
gate accepts only a designated verifier's opaque receipt decision naming the PR and exact head SHA.
A push invalidates that verification. Rights review can proceed alongside technical review; merge
is the only blocked transition.

The maintained DCO App owns DCO identity/trailer verification. Vexa does not reproduce that logic
with a regular expression and does not allow third parties or maintainers to sign for an author.
Because the hosted app exposes a write-user override that cannot be disabled in repository
configuration, a second required `dco-no-override` check accepts only the app's ordinary verified
success and fails closed on manual overrides or unknown success messages. The local rights gate
otherwise owns only path selection, review state, and head-bound corporate receipts.

The policy is prospective at the bootstrap PR number. Historical work is risk-triaged and never
rewritten merely to add sign-offs.

## Consequences

- The ordinary contributor makes one explicit legal choice and uses standard Git sign-off.
- Corporate authorization becomes attributable, private, and invalidated by code changes.
- DCO App installation, required-check configuration, a private register, and maintainer adoption
  of the corporate instrument — a standard text from a recognized trusted party, minimally adapted,
  with its exact version and SHA-256 pinned — remain activation prerequisites outside the
  repository.
- A bootstrap PR can prove the deterministic machinery locally; a post-merge canary PR is required
  to witness GitHub event, Check Runs, DCO App, and branch-protection behavior end to end.
