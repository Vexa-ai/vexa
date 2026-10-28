# ADR 0044 — Vendoring held identical by parity is how Python code crosses images

**Status:** accepted · 2026-10-10 · for v0.13.2 ([#1784](https://github.com/Vexa-ai/vexa/pull/1784),
[#1783](https://github.com/Vexa-ai/vexa/issues/1783)) · applies P2, P8 and P23

## Context

Several separately built images need the same Python code: the identity token verifier, the
delegation token, the config preflight, the transcript segment signer, the broker's assertion and
setup schema, the git runner, the work-tree path helper and the outbound URL guard. The repository
has no way to share Python between images: no `pyproject.toml` has a path dependency, and P2 forbids
one service importing another's internals. What the repository does instead is copy the module
verbatim into each image and let `gate:fact-parity` fail the build when a copy differs. At the time
of this decision that is ten `file-bytes` facts holding thirty-nine copies.

No decision recorded that mechanism, when to use it, or where the copy everyone edits lives. The
sixth architecture pass on #1784 found the cost of that gap: the outbound URL guard's canonical copy
sat inside meeting-api's private webhooks package, so four other images took their address rule from
one consumer's internals, and its case table called itself a `.v1` contract while being an unsealed
test fixture.

## Decision

1. **A Python module several images need is vendored verbatim, and a `file-bytes` fact in
   `scripts/parity.json` lists every copy.** A copy is never edited in place: the canonical copy is
   edited and copied over every site, in one change, and every consumer's tests run.
2. **The canonical copy lives where the rule it implements is defined**: the contract directory of
   the wire or rule (`gateway-identity.v1/identity_token.py`, `delegation.v1/delegation.py`,
   `transcript.v1/segment_entry.py`, `credential-broker.v1/assertion.py`, `config.v1/preflight.py`,
   `outbound-url.v1/ssrf.py`), or the shared package of the mechanism that owns it
   (`core/workspaces/shared/workspace_paths.py`). Never inside one consumer's private package.
3. **When the rule is also implemented in another language, the two share a case table, not code.**
   The table is the golden of the rule's contract; each language's suite reads a copy held by its
   own parity fact, and the contract's `validate.mjs` holds every copy to the schema.
4. **Vendor only what is small, standard-library-first and stateless**: a rule, a codec, a verifier.
   Code with dependencies of its own, state, or a lifecycle is a service behind a contract instead.

## Consequences

- **What the seal does not cover.** `contracts.seal.json` hashes a contract's schemas, so a canonical
  module in a contract directory, and a golden table, change without a seal diff. They are held by
  their parity fact and by every consumer's tests, not by `gate:contract-version`; a change to one is
  reviewed as code.
- **Cost.** Every copy ships in its image and every change touches every site. The gate makes a
  forgotten site a red build, not a silent drift.
- **Not the end state.** One installable package (for example a uv path dependency built into each
  image) would replace the copies. That is a build change across five or more images and is left for
  after v0.13.2.
- The outbound URL guard moved under this decision: `deploy/contracts/outbound-url.v1` holds the
  canonical `ssrf.py` and the case table, sealed as a contract; meeting-api keeps a vendored copy
  like every other consumer.
