<!-- The PR carries TWO artifacts, judged on different axes (docs/docs/governance/delivery.mdx D8):
     the OBSERVATION BUNDLE answers "is the value real?"; the DIFF answers "is it correct and safe?".
     A diff with no bundle is not reviewable. -->

> 👋 **Highly recommended (not required): say hi on [Discord](https://discord.gg/Ga9duGkVz9)** and
> tell us, in a sentence or two, **what** you're changing and **why** — the story behind the diff.
> It makes reviewing your value bundle faster and connects you to the reporter and the maintainers.
> Your PR is judged on its evidence, not on whether you show up — but showing up helps a lot.

**Delivers issue:** #

**Peer reviewers:** @
<!-- Who you are asking to review the diff. Anyone who is not you. A triager
     (MAINTAINERS.md) satisfies the roster half of the merge rule. Leave it blank and the
     community board will pick it up — naming someone just makes it faster. -->

## Value claim
<!-- One or two sentences, three parts, all required. This is what a validator tests — not
     the diff. "Refactors X" is not a value claim. "Nobody asked for this" is an honest
     one; write it. -->

- **What job:** <!-- the job this does, in the user's words, not the code's -->
- **For whom:** <!-- who has that job: a named reporter, a deployment shape, a role -->
- **How I know:** <!-- the evidence it is real: the issue, the thread, the log, the run -->

## How to validate
<!-- Write this for a stranger with a laptop and no context. All three lines required.
     Full playbook: VALIDATION.md -->

- **Run:** `make lite` against this PR (or `make dev` from the branch), then …
- **Look at:**
- **Pass means:**

<!-- VALIDATORS: post your result as ONE comment whose FIRST line is exactly
       Validated on <lite|compose|helm|hosted>, <what you ran>, <result>
     with the evidence underneath. An agent's tests are not human evidence — a validation
     names the human who ran it. See VALIDATION.md. -->

## Contribution rights
<!-- Select exactly ONE. This is a legal certification: an agent may explain the choices but
     must not select one for you. See CONTRIBUTOR_RIGHTS.md. -->

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
