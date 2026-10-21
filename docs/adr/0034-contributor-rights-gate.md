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
the same individual DCO plus a short employer authorization letter the contributor forwards to
their own approvers; Vexa publishes no contributor agreement and asks no one to sign one. The public
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

## Addendum 2026-10-02 — declared once per contributor

Founder ruling: the choice is made once per contributor, not on every pull request. The gate now
passes a pull request without a selection when an earlier merged pull request by the same author
carried an independent declaration with no unresolved review and a body edited only by that author.
Nobody maintains independent contributors by hand. The `contributors` map in
`.github/contribution-rights.json` exists only for the maintainer's standing declaration and for
corporate authorizations that cover future work, which are rare and verified against the private
register before a maintainer adds them with their `VCR-` receipt. A head-bound corporate receipt on
an earlier pull request never carries forward. The check names the pull request or entry it relied
on. The registry is read from the default branch, so a pull request cannot register its own author.

A standing covers only its owner's commits. A commit by another GitHub author with no standing of
their own returns the pull request to the per-PR selection. An explicit selection that differs from
the standing decides that pull request, an unresolved verifier review still blocks, grandfathering
is unchanged, and DCO remains a separate check.

## Addendum 2026-10-03 — the verdict lives on the evaluated head

Comments and DCO results trigger the gate through `issue_comment` and `check_run`, and GitHub
attaches those runs to the default branch's newest commit. The gate therefore never fails its own
job on a verdict: the published `contribution-rights` check run on the pull request head, or on the
merge-group head, is the only verdict. A failed GitHub read while evaluating a pull request is
published there as a failing verdict that names the error. Runs from comments and check results each
get their own concurrency group, so they are never cancelled by one another; pull request and
merge-group runs still supersede earlier runs for the same head, and the merge queue re-evaluates
rights before anything lands.
