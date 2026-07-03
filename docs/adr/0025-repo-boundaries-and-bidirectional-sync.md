# ADR-0025 — Repo boundaries and bidirectional sync

**Status:** Proposed · 2026-07-03

## Context

One product, three repos. `Vexa-ai/vexa` (this monorepo's public home) is the community
superset where product development happens; `Vexa-ai/vexa-core` is the FINOS-governed
curated projection that regulated adopters consume; and the maintainer runs a private
operations workspace. The seams between them were never written down, and it shows:
community triage load on vexa (~149 open issues, 34 unlabeled; 20 open PRs) exceeds what
ad-hoc maintainer attention absorbs; the FINOS projection must stay **curated** — every
file it carries deliberately placed, gates green, no personal-infra leakage — which rules
out naive mirroring; and the carve harness that produces the projection lived in-tree at
`carve/` with personal-rig defaults hardcoded (`/home/dima/...` paths, a hardcoded
committer identity), making it both unshippable as-is and a wrong resident of a product
repo. Without stated boundaries, every sync, triage batch, and governance edit is an
improvisation.

## Decision

### 1. The repo table and path ownership

| Repo | Visibility | Role | Accepts |
|---|---|---|---|
| `Vexa-ai/vexa` | public | community **superset**; **SSOT for product code** | product issues/PRs, all feature work |
| `Vexa-ai/vexa-core` | public | **FINOS-governed curated projection** | sync trains; governance-file changes |
| ops workspace (private) | private | maintainer operations (see §6) | sync/triage tooling, policies, run-state |

**Path ownership is exclusive.** Product code's home is **vexa**; governance files' home
is **vexa-core**. The normative governance-file list (home = vexa-core):

`LICENSE`, `NOTICE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `MAINTAINERS.md`,
`SECURITY.md`, `security-insights.yml`, `security/`, `ARCHITECTURE.md`.

Each side may hold a **mirror** of the other's files for losslessness of the projection,
but **changes originate only in the home repo** — a product change opened against
vexa-core gets rerouted upstream; a governance edit riding a train is a defect.

### 2. Sync directions

- **vexa → vexa-core:** **on-demand, owner-triggered curated trains.** A train is a
  reviewed batch of monorepo commits replayed by the carve harness — **one PR per
  train**, gates must pass, proposal reviewed (FLAG rulings) before cutting. No
  continuous mirror, no bot pushes.
- **vexa-core → vexa:** **continuous back-sync.** Community contributions merged in
  vexa-core flow back to the monorepo via the `sync/vexa-core-backport` branch as normal
  merges.

### 3. Authorship invariants (normative)

1. **Never squash sync PRs** — squash re-attributes replayed commits to the merger and
   erases contributor authorship. **Squash-merge is disabled on vexa-core** (repo
   setting).
2. **Preserve `Author`, `AuthorDate`, and the commit message** of every replayed commit
   verbatim (trailers below may be appended; nothing else changes).
3. **Committer = the syncing maintainer** — replay sets the committer identity to
   whoever runs the sync, keeping "who wrote it" and "who moved it" distinguishable.
4. **`Origin: <repo>@<sha>` trailer on every replayed commit** (e.g.
   `Origin: DmitriyG228/vexa@<full-sha>`) — provenance is recoverable from the commit
   itself, not from PR archaeology.
5. **Mailmap normalization** — placeholder/rig identities normalize to real contributor
   identities at replay time, never by post-publish rewrite.
6. **vexa `main` is append-only** — no force-push, no history rewrite on published
   branches.

### 4. Access levels for maintainer operations

Every operation the maintainer-operations agent performs against these repos carries one
of three levels, enforced by GitHub App token scopes:

| Level | Covers | Approval | App token scopes |
|---|---|---|---|
| **L1** | all reads (`gh` GETs: issues, PRs, checks, files) | none — always allowed | read-only (`contents:read`, `issues:read`, `pull_requests:read`, `metadata:read`) |
| **L2** | comments, labels, closes on public repos | **batched**: agent emits a verdict table and stops; a human approves rows; only approved rows execute | + `issues:write`, `pull_requests:write` |
| **L3** | pushes, merges, PR creation, releases, settings | **per-action**: one approval = one action | + `contents:write` (settings additionally `administration:write`, transient) |

Unclassifiable actions default to L3. Scope enforcement is technical, not honorary: a
session approved at L1 holds a token that cannot write.

### 5. DCO

Both public repos adopt the **DCO check** (`Signed-off-by` on every commit).
**In-flight PRs are grandfathered** — enforcement applies from the adoption date forward,
so no contributor is retroactively failed. The carve harness already commits with `-s`;
**DCO-on-sync is explicitly the *committer's* sign-off over preserved authorship**: the
syncing maintainer certifies the right to relay the contribution (DCO §11(b)/(c)), while
the original `Author` line continues to attribute the work.

### 6. The ops workspace

Maintainer operation lives in a **private workspace bundle repo** running on the vexa
runtime (attached via `swap_workspace`). Sync tooling (the carve harness), triage
procedure, access policies, and operational run-state live **there, not in product
repos** — product repos carry product. **Explicit carve-out:** repo-local workspace
*protocol* files — `CLAUDE.md`, the workspace seeds under `core/agent/workspace-seeds/`
— are **product content** (they define what a workspace *is*) and stay in the product
repos.

## Consequences

- The carve harness **relocates to the ops bundle** (`ops/carve/`, env-contracted, no
  personal-rig defaults); `carve/` and `scripts/sync-carve.sh` are **removed from this
  monorepo in a follow-up PR**.
- `docs/adr/0025-…` and `docs/adr/README.md` enter the carve **file-granularly**, so
  vexa-core carries the boundary decision that governs it.
- vexa-core PRs **#26–#30 close as carried-by-train** once the backport branch merges;
  **#32** is back-synced first, then closes the same way.
- **Squash-merge is disabled** on vexa-core (settings change, L3).
- `CARVE_REMOTE`, committer identity, and the repo-settings expectations are documented
  in the ops bundle (`ops/carve/carve.env.example`, `ops/carve/README.md`) — this repo
  carries no rig configuration.
- Triage and governance procedures (state machine, SLAs, verdict batches) live in the
  ops bundle's ADR-1 and playbooks; this ADR only fixes *where* they live and *what they
  may touch*.
