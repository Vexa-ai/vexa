# AGENTS.md — the agent front door (in-repo)

Two constitutions govern everything here — read them on the docs site or in this tree:

- **[Architecture](https://docs.vexa.ai/governance/architecture)** — the P-book
  ([`docs/docs/governance/architecture.mdx`](docs/docs/governance/architecture.mdx)): what the
  software must be (P1–P23, enforced by the CI gate suite). Your change must fit these.
- **[Delivery](https://docs.vexa.ai/governance/delivery)** — the D-book
  ([`docs/docs/governance/delivery.mdx`](docs/docs/governance/delivery.mdx)): how change ships —
  the whole loop (roadmap · PREPARE · TAKE · validation · merge bar · ship bar) in one file.

This file is the actor contract: intake, claim, and how a session behaves in the checkout.

## Researching Vexa? Start here

- **Found something** (bug, gap, docs lie)? **File a GitHub issue** — every report enters
  `state: incoming` and a 3-day triage SLA. Findings are contributions.
- **Want to contribute?** The whole roadmap comes back in one public GraphQL call:

```bash
gh api graphql -f query='
query { organization(login: "Vexa-ai") { projectV2(number: 2) {
  items(first: 100) { nodes {
    content {
      ... on Issue { number title url state milestone { title } labels(first: 10) { nodes { name } } }
      ... on DraftIssue { title body }
    }
    fieldValues(first: 20) { nodes {
      ... on ProjectV2ItemFieldSingleSelectValue {
        field { ... on ProjectV2SingleSelectField { name } } name }
    } }
  } }
} } }'
```

Four axes per item: **Lane** — the business value, named by who feels it (Google Meet · MS Teams
· Zoom · API integrators · Transcription · Recordings · Webhooks & billing · First run ·
Production ops · Feature shelf) — match it to what your contributor actually uses. **Human bar**
— what validation costs in human terms (Desk check · Operator run · Solo meeting · Small group ·
Crowd 5+ · In-the-loop UX). **Setup** — the infrastructure floor (None · Lite · Compose ·
k8s/helm). **Milestone** — the version it gates (currently `v0.12.x`).

Selection rule: filter to your contributor's lane(s), then to the Human bar and Setup they can
afford. `state: ready` is claimable now; `state: prepared` after the maintainer stamp; items
with no issue number are declared direction — ask about them, don't claim them.

## Your issue is your PRD

A prepared issue is a worked delivery spec — read it end to end before touching code:

- **Where we are (honest)** — code-grounded claims (`file:line`), era notes where a report
  predates the tree. Spot-check them; a wrong claim is a finding, report it on the issue.
- **Components** — your waypoints: one module/seam each, with the existing harness + fixtures
  you compose (you don't invent scaffolding).
- **Prepared solution + forks** — the mechanism and the branches you may actually hit.
  Alternates welcome, never required.
- **The acceptance table — your definition of done.** Present its observations (red→green
  pairs, negative controls, anchors) and the PR **merges — that's a promise** (D10). If your
  bundle satisfies the table and something is still wrong, that's *our* table bug, not yours.
- **Deployments to validate · docs surface · preferred validator** — the change isn't done
  until its docs move with it and someone is named to witness it.

## Claiming — and say hello on Discord

1. **Comment on the issue** to claim it (D14b).
2. **Announce it on [Discord](https://discord.gg/vexa)** — strongly recommended: one line,
   "taking #NNN". This opens the human-to-human channel; questions, steering, and validation
   scheduling all move faster once a maintainer knows a person, not just a branch.
3. **Heartbeat = visible activity** on the issue/PR. Hours of silence release the claim back
   to the queue — no hard feelings, no stuck work. Going quiet deliberately? Say so on the
   issue and the lease holds.

## How you work the checkout

- **Discord is the working channel** — blockers, steering, quick questions live there while you
  work. One rule keeps the record honest: **anything decided lands back on the issue**, or it
  didn't happen. The issue is the source of truth; Discord is the speed.
- **One worktree per session** (`git worktree add ../vexa-<slug> -b <your-branch>`). Never two
  sessions on one tree; never adopt another session's uncommitted files — surface them.
- **Read the chart first.** [`docs/views/architecture.dsl`](docs/views/architecture.dsl)
  (~1.4k tokens) is the generated whole-graph index: every node, edge, carrier, owner. For a
  slice: `pnpm arch:viz cluster:<domain>`. If your change adds/moves a module or alters a
  data flow, update `architecture.calm.json` in the SAME change and `pnpm seal:arch` (P23).
- **The loop — expect before you act.** For every objective, write the falsifiable **Expected**
  first, act, then record the **Actual** (raw evidence: commands, outputs, counts — and what
  you did NOT check), and a **Verdict**. Expected ⇒ continue; unexpected ⇒ stop and interpret —
  an unexpected result is a finding, never something to paper over. Unfinished scaffolding you
  were always meant to complete is NOT "unexpected" — finish it; stop only on genuine
  contradiction.
- **Gates green before push:** `node scripts/gates.mjs all` (also runs on pre-push). Green is
  necessary, never sufficient — prove user-facing behavior at the altitude of the claim (P19):
  a live leg for live behavior, not just unit green.

## The hard rules (from the P-book — the ones sessions trip on)

- **Fix at the point of introduction, never the point of observation.** A defect surfaces in a
  consumer but is born in a producer. Trace it hop-by-hop back to where it's introduced, then
  fix there. Never patch a consumer to compensate for a producer's bug — we don't work around
  our own bugs. Reproduce without a live meeting before you fix.
- **The core owns its contracts; clients adapt.** A consumer's legacy shape is translated at
  the client boundary, never pushed upstream into the core.
- **Brick front doors are per-runtime; inject runtime dependencies.** Browser-reachable front
  doors are types-only; node-only capability lives behind a subpath; cross-brick runtime deps
  are injected. The bundler is the gate a logic test passes right over.
- **Source states the designed present, not its history.** No "this used to be X", no bug
  archaeology in comments — write code as if it had always been this way.
- **Report facts, then your reading — labelled as yours.** State the objective, ship raw
  evidence (including what was NOT checked), and put "done/works" downstream of the data (P21).

## Delivering — the PR

**Witness your own value first.** Before opening the PR, run your change live at the issue's
declared human bar — your own run is the bundle's first row. You never ask another human to
witness value you haven't witnessed yourself.

Then the PR carries two artifacts, judged on different axes (D8):

1. **The observation bundle** — your acceptance table mapped row-by-row to evidence, in the
   issue's own numbering, your live witness included. This answers *"is the value real?"*
2. **The diff** — which passes review and the security checks the issue names. This answers
   *"is it correct and safe?"*

A diff with no bundle is not reviewable. Authorship: agents are instruments — **what you ship
is yours**, full responsibility, honored as full authorship and credit; no agent co-author
trailers (D13).

The maintainer triages your PR by the
[TAKE protocol](https://docs.vexa.ai/governance/delivery#take-protocol) — read it to know
exactly how you'll be read. Then a **non-author validates the value**
([the attestation shape](https://docs.vexa.ai/governance/delivery#validation)); validators are
credited in release notes alongside you, and the reporter of the original bug is the preferred
signer of your fix.
