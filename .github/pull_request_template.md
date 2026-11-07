<!-- The PR carries TWO artifacts, judged on different axes (docs/docs/governance/delivery.mdx D8):
     the OBSERVATION BUNDLE answers "is the value real?"; the DIFF answers "is it correct and safe?".
     A diff with no bundle is not reviewable. -->

> 👋 **Highly recommended (not required): say hi on [Discord](https://discord.gg/Ga9duGkVz9)** and
> tell us, in a sentence or two, **what** you're changing and **why** — the story behind the diff.
> It makes reviewing your value bundle faster and connects you to the reporter and the maintainers.
> Your PR is judged on its evidence, not on whether you show up — but showing up helps a lot.

**Delivers issue:** #

## Contribution rights (first contribution only)

Declared once per contributor, not per PR. Leave this section unticked if you selected Independent
yourself on an earlier merged PR here, or hold a corporate authorization on file, unless this
contribution's rights differ. Otherwise select exactly one. See CONTRIBUTOR_RIGHTS.md.

- [ ] **Independent:** I created this contribution, or otherwise have the right to submit it
  under Apache-2.0, and it is not owned or controlled by an employer, client, or other entity.
  <!-- rights:independent -->
- [ ] **Employer/client authorization required:** an employer, client, or other entity owns or
  may control this contribution. I am requesting Vexa's private corporate-authorization process.
  <!-- rights:corporate -->
- [ ] **Unsure:** I need a private rights review before merge.
  <!-- rights:uncertain -->

Every commit must also carry the contributor's own DCO `Signed-off-by` line. Selecting the
independent path means no individual CLA is required. Corporate and unsure paths do not stop
technical review, but they do block merge until resolved.

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
     say why. The diff review covers the docs story (right pages, right altitude, consistent
     with how the docs teach), and so does a live validation when one is run (D6c). -->

## Security checks (required on the diff)
<!-- Dependency/licence scan, secrets scan, SAST where it applies — show the runs.
     The maintainer runs the closing security bundle before release. -->

## Validation request (optional, credited)
<!-- Who is invited to witness the value live (any competent non-author; the originating reporter
     is the preferred validator) and what they'll watch. A validation is optional and credited in
     release notes beside you (D9). It is not a step before merge, and no label or gate waits for
     it. When run, it is multichannel and its channels must corroborate: a human/instrument
     divergence is a credited finding (D9/D11), filed on the PR or as an issue. It does not block
     merge by itself; if it shows the change is wrong, that reaches the merge card as a red
     value-fsm, a `fail` pass marker or a changes-requested review. When run, it also covers the
     docs story (D6c): the right pages, at the right altitudes, consistent with how the docs teach.
     D12b: state the DEPLOYMENT validated (Lite / full compose / k8s / hosted) and its build
     provenance — repo sha, compose or values file, fresh-clone vs long-lived, env deltas from
     stock. Deployments the issue names but nobody ran stay honestly unclaimed here. -->

## Authorship
Sole author: the human submitting this. No agent co-author trailers (D13).
Tooling disclosure (optional, welcome, never an attribution): …
