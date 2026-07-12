# AGENTS.md — the contributor's operating file (post-clone)

You have a checkout. Two constitutions govern everything in it:

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the P-book: what the software must be
  (principles P1–P23, enforced by the CI gate suite). Your change must be a perfect fit to these.
- **[docs/DELIVERY.md](docs/DELIVERY.md)** — the D-book: how change ships (D0–D17: the
  issue → PR → proof → release loop). Your work travels through this.

This file is the actor contract between them: how a session — human, agent, or both —
behaves in the checkout. (Pre-clone, remote agents start at [`llms.txt`](llms.txt) instead;
the contributor-facing overview is
[docs/docs/roadmap/contributing.mdx](docs/docs/roadmap/contributing.mdx).)

## Your issue is your PRD

Work starts from a **`state: ready`** issue on the
[roadmap board](https://github.com/orgs/Vexa-ai/projects/2). A prepared issue is a worked
delivery spec — read it end to end before touching code:

- **Where we are (honest)** — code-grounded claims (`file:line`), era notes where a report
  predates the tree. Spot-check them; a wrong claim is a finding, report it on the issue.
- **Components** — your waypoints. Each names one module/seam and the existing harness +
  fixtures you compose (you don't invent scaffolding).
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

Two artifacts, judged on different axes (D8):

1. **The observation bundle** — your acceptance table mapped row-by-row to evidence, in the
   issue's own numbering. This answers *"is the value real?"*
2. **The diff** — which passes review and the security checks the issue names. This answers
   *"is it correct and safe?"*

A diff with no bundle is not reviewable. Authorship: agents are instruments — **what you ship
is yours**, full responsibility, honored as full authorship and credit; no agent co-author
trailers (D13).

The maintainer triages your PR against the prepared issue by
**[docs/DELIVERY-TAKE.md](docs/DELIVERY-TAKE.md)** — read it to know exactly how you'll be
read. Then a **non-author validates the value live** (multichannel, provenance-anchored — see
the [contributing page](docs/docs/roadmap/contributing.mdx)); validators are credited in the
release notes alongside you, and the reporter of the original bug is the preferred signer of
your fix.
