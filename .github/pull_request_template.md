<!-- The PR carries TWO artifacts, judged on different axes (docs/docs/governance/delivery.mdx D8):
     the OBSERVATION BUNDLE answers "is the value real?"; the DIFF answers "is it correct and safe?".
     A diff with no bundle is not reviewable. -->

> 👋 **Highly recommended (not required): say hi on [Discord](https://discord.gg/Ga9duGkVz9)** and
> tell us, in a sentence or two, **what** you're changing and **why** — the story behind the diff.
> It makes reviewing your value bundle faster and connects you to the reporter and the maintainers.
> Your PR is judged on its evidence, not on whether you show up — but showing up helps a lot.

**Delivers issue:** #

## Observation bundle (the record of your harnessed loop)
<!-- One entry per component: what you ran, what you saw with your own eyes, what it told you
     about the next step. Your claim heartbeats are the natural front of this. A component that
     proved unnecessary, with evidence, is a completed waypoint. -->

- **C1 —** ran: … · saw: … · concluded: …

## Acceptance floor
<!-- Map each row of the issue's acceptance table to its evidence (red→green outputs with base+head
     shas, negative controls shown red, anchors). Rows you exceeded with NEW witnessed value:
     welcome — describe them, that's the system working. -->

| Row | Evidence |
|---|---|
| A1 |  |

## Docs diff (D6c)
<!-- The pages this PR updates, mapped to the issue's docs surface — quickstart / how-to /
     reference / concept, each at its reader's altitude. If a named page needed no change,
     say why. The validator signs the docs story alongside the value. -->

## Security checks (required on the diff)
<!-- Dependency/licence scan, secrets scan, SAST where it applies — show the runs.
     The maintainer runs the closing security bundle before release. -->

## Validation request
<!-- Who should witness the value (any competent non-author; the originating reporter preferred)
     and what they'll watch. The attestation must corroborate with the instrument channels —
     a human/instrument divergence blocks merge until reconciled, and is a finding.
     The attestation also covers the docs story (D6c): the update lands on the right pages, at the
     right altitudes, consistent with how the docs already teach.
     D12b: state the DEPLOYMENT validated (Lite / full compose / k8s / hosted) and its build
     provenance — repo sha, compose or values file, fresh-clone vs long-lived, env deltas from
     stock. Deployments the issue names but nobody ran stay honestly unclaimed here. -->

## Authorship
Sole author: the human submitting this. No agent co-author trailers (D13).
Tooling disclosure (optional, welcome, never an attribution): …
