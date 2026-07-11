# Delivery compliance — principle → gate → status

> Companion to [`DELIVERY.md`](DELIVERY.md), the way [`ARCH-COMPLIANCE.md`](ARCH-COMPLIANCE.md)
> companions the architecture book (D1: an ungated rule is aspirational — this table is the
> honest map). Hand-maintained until the checks land; each TO BUILD row links the machinery work.

| Principle | Gate | Status |
|---|---|---|
| D1 enforced-not-aspirational | this table stays current in every delivery PR | **have** (manual) |
| D-A targets resolve to real modules | preparation review: `Target:` paths exist | **have** (manual review) |
| D-A2 fixture ranges | preparation review: early validations name fixtures | **have** (manual review) |
| D-R0 two species | branch protection (merge) · `state: ready` maintainer-only | **have** (branch protection) / **TO BUILD** (label permission bot) |
| D2 one ordered queue | the Vexa Roadmap project board is the single source | **have** (board live) / **TO BUILD** (coverage check) |
| D2b 3-day intake SLA | intake bot ages `state: incoming` | **TO BUILD** |
| D3/D4 business meaning + code grounding | preparation review checklist | **have** (manual) |
| D5/D6 issue shape | issue template with required sections | **TO BUILD** (template) |
| D7 principle check on fixes | fix-request template section | **TO BUILD** (template) |
| D8 bundle + diff | PR template requires the observation bundle | **TO BUILD** (template) |
| D-S security lanes | contributor security status + maintainer bundle | **TO BUILD** (status) / **have** (maintainer practice) |
| D9 value-signed, corroborated | `gate:value-signed` checks non-author + channel consistency | **TO BUILD** (value-gate bot) |
| D10 acceptance floor | bundle-vs-table check | **TO BUILD** (bundle checker) |
| D11 both verdicts credited | release-notes generation from bundles | **TO BUILD** |
| D12 altitude-scaled validation | stated live bar in every prepared issue | **have** (manual review) |
| D13 human sole author | commit-trailer check (reject agent co-authors) | **TO BUILD** |
| D14/D14b state machine + lease | label bot + lease bot | **TO BUILD** |
| D15 release machinery | release-set gate · `release/vm-validated` | **in flight** (PR #494) |
| D16 close-back to reporter | ship-closes-report step | **TO BUILD** |
| D17 declined-with-reason | closed-without-merge requires one `declined:` label | **have** (manual) / **TO BUILD** (stale proposer) |

Machinery tracking: the TO BUILD rows are one work item (the delivery checks bot: label state
machine, lease TTL, value gate, bundle checker, intake/stale agers, trailer check) — tracked on
the roadmap as delivery-machinery work.
