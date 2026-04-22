# Groom — 260422-release-plumbing

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| release_id   | `260422-release-plumbing`                                              |
| stage        | `groom`                                                                |
| entered_at   | `2026-04-22T17:35:10Z`                                                 |
| actor        | `AI:groom`                                                             |
| predecessor  | `idle` (worktree-bootstrap — fresh per-release checkout)                |
| theme (AI)   | *"Release plumbing — chart consumers get fresh, versioned charts; Vexa devs get parallel release worktrees out of the box."* |

---

## (A) Product framing

### Elevator pitch

**"Fresh charts for consumers. Parallel releases for devs. One tight
cycle that fixes both drift problems."**

### What we deliver

Two small, orthogonal plumbing fixes that together remove silent drift
from the dev→consumer path:

1. **Helm chart gets a real version on every release** (#228). Today
   `Chart.yaml` has been pinned at `0.1.0` for ~100 commits across
   four tagged releases (`v0.10`, `v0.10.1`, `v0.10.2`, `v0.10.3`).
   Consumers who vendor the chart have no way to detect they're 9 days
   behind `main` — as happened in `vexa-platform` before the 2026-04-21
   DB-pool incident. After this cycle: `Chart.yaml.version` bumps per
   chart-touching commit (SemVer), a packaged `.tgz` is published via
   GitHub Pages as a Helm repo, and consumers can pin `helm repo add
   vexa https://vexa-ai.github.io/vexa` like every other OSS chart.

2. **Per-release git worktrees, scalable by default** (#229). Today
   `tests3/lib/vm.sh` and `lib/lke.sh` label throwaway Linode infra by
   `HHMM` timestamp — two devs provisioning in the same minute generate
   identical labels, and the console becomes unreadable. After this
   cycle: labels carry `release_id + short random suffix` (traceable +
   collision-free), and `make release-worktree ID=<id>` creates a
   dedicated `../vexa-<id>` working dir per release so N releases run
   in parallel from one clone — each with its own `.current-stage`,
   `.state/`, and infra namespace. This release is itself the first to
   dogfood the pattern.

### Who wins

- **Self-hosters + chart consumers** — `helm upgrade` actually tells
  them when they're behind; the N-1-(a) action from the 2026-04-21
  incident post-mortem lands upstream.
- **Vexa devs running parallel releases** — can now do `release-worktree`
  once and iterate on N releases simultaneously without state collision.
  The `260422-zoom-sdk` cycle is already active; this release proves
  the pattern works without blocking it.
- **Future groom/plan runs** — have a dedicated working dir seeded at
  `idle`, so `make release-groom` is a true cold-start rather than
  "hope the main checkout is on the right branch".

### Who sees no change

- **End users of app.vexa.ai.** No runtime, schema, or API behaviour
  changes. No new env vars. No deprecation warnings.
- **Consumers pinning `vexa-0.1.0.tgz` today.** That tarball continues
  to work. They opt into the new publish path when they pin a real
  version.
- **In-flight release `260422-zoom-sdk`.** Runs to completion in the
  main checkout; this cycle's worktree sits beside it.

---

## Scope, stated plainly

Two issues. Both are plumbing: they remove silent drift in dev-facing
workflows, they don't change service behaviour.

- **#229** — `tests3/lib/vm.sh` / `lib/lke.sh` label throwaway infra
  with `vexa-t3-<mode>-HHMM`, which collides between parallel clones.
  Reporter's ask (`release_id + random suffix`) plus the corollary
  the reporter hinted at: formalize per-release git worktrees as the
  scaling unit (`../vexa-<id>`), since that's where `.current-stage`
  and `.state/` already diverge cleanly.

- **#228** — `Chart.yaml` pinned at `0.1.0` for ~100 commits; no
  published chart artifact. Consumers can't detect drift. Reporter's
  ask: bump SemVer per chart-touching commit **and** publish a
  packaged `.tgz` (GitHub Releases or gh-pages Helm repo).

Both reporters offered PRs and asked for direction first. This cycle
provides that direction and ships reporter-validated shape.

Both are cheap (≤ half-day each). Both are independent (one touches
`deploy/helm/`, the other touches `tests3/lib/`). Both unblock
downstream workflows that are measurably broken today.

---

## Signal sources scanned

| source                                                               | count | notes                                                            |
|----------------------------------------------------------------------|------:|------------------------------------------------------------------|
| `gh issue view 228,229`                                              |     2 | Both filed 2026-04-22; both explicitly "willing to contribute, please confirm direction". |
| `git log e3d4eec..HEAD`                                              |     — | main checkout on `dev`, cycle `260422-zoom-sdk` mid-flight; no overlap with these two. |
| Prior incident post-mortem (`vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md`) | 1 | §N-1 action item = "publish chart artifacts upstream" → exactly Pack B here. |
| Prior groom `260421-prod-stabilize`                                  |     — | Packs A + D + G all touched `deploy/helm/` templates; version bump discipline would have made that release reviewable as a chart bump. |
| Discord                                                              |     — | no in-repo fetcher yet (README §4.2 marks as future work); skipped |

---

## Packs — candidates for this cycle

### Pack A — tests3 throwaway-infra label collision + per-release worktrees (**recommended: YES, P2 — dev-experience, cheap**)

- **source**: issue [#229](https://github.com/Vexa-ai/vexa/issues/229),
  filed 2026-04-22 with a concrete proposal.
- **symptom**: `tests3/lib/vm.sh` line 42 uses `vexa-t3-${mode}-$(date
  +%H%M)`. `tests3/lib/lke.sh` line 46 uses `vexa-t3-$(date +%H%M)`.
  Two devs running `make release-provision` within the same HHMM
  window get identical labels. Linode de-dups by id, not label —
  both clusters coexist — but the Linode console becomes unreadable,
  and any future tool that did dedup by label would silently destroy
  the other dev's infra.
- **severity**: **P2** — not a production fire, but the single
  collision point preventing "just clone and go" multi-dev work. The
  rest of the tests3 paradigm (per-clone `.current-stage`, `.state/`,
  throwaway infra) already supports N-parallel cleanly.
- **scope shape (groom view; plan assigns DoDs)**:
  - **A.1 (#229 literal)** — add a `release_label PREFIX` helper in
    `tests3/lib/common.sh` that reads `release_id` from
    `.current-stage` and composes `PREFIX-<release_id|adhoc>-<hex6>`.
    Replace the two timestamp-only labels with calls into the helper.
    Result: `vexa-t3-compose-260422-release-plumbing-a3f9c2` — unique,
    traceable, console-readable.
  - **A.2 (corollary)** — formalize `../vexa-<release_id>` git
    worktrees as the protocol-level unit of parallelism. `vm_destroy`
    and `lke_destroy` already delete by id (not by label), so the
    label is now purely a human identifier; the machine identifier is
    `.state/vm_id` / `.state/lke_id` which lives per-worktree. Add
    `tests3/lib/worktree.sh create <release_id>` that does
    `git worktree add` + `stage.py enter idle --release <id>`. Add
    `make release-worktree ID=<id>` as stage-0a in the Makefile.
    Update `stages/00-idle.md` Next + `stages/01-groom.md` Step 0 to
    describe the flow. This release dogfoods the pattern.
- **estimated scope**: A.1 ≈ 3 edits (common.sh helper + vm.sh + lke.sh).
  A.2 ≈ worktree.sh + Makefile target + 2 stage-doc edits. **~0.5 day
  including smoke test.**
- **repro confidence**: HIGH — reporter gave the exact label string and
  the exact code lines. A.2 builds on A.1 with no new repro needed
  (pure tooling).
- **owner feature(s)**: `tests3` itself — this is release plumbing, not
  a product feature. No existing feature folder to attach to; plan
  decides whether a `tests3/` pseudo-feature with DoDs is appropriate
  or whether this rides as a protocol change with a registry check
  only.
- **new check candidates (plan's job)**:
  - `tests3.labels.unique-per-release` — rendered label string
    contains `release_id` segment; random suffix is 6 hex chars.
  - `tests3.worktree.bootstrap` — `make release-worktree ID=<id>`
    creates `../vexa-<id>` with `.current-stage` seeded at `stage:
    idle, release_id: <id>`.

### Pack B — Helm chart versioning + publishing (**recommended: YES, P1 — consumer-facing, half-day**)

- **source**: issue [#228](https://github.com/Vexa-ai/vexa/issues/228),
  filed 2026-04-22. Reporter observed 9-day drift in `vexa-platform`
  before 2026-04-21 incident — a concrete blast radius, not a
  hypothetical.
- **symptom**: `deploy/helm/charts/vexa/Chart.yaml` has been pinned at
  `version: 0.1.0` since commit `bf3dd83` (~100 commits ago) across
  four repo release tags (`v0.10` → `v0.10.3`). No published chart
  artifact — consumers run `helm package` against a clone. `helm
  dependency update` compares version strings, so pinning
  `version: 0.1.0` silently freezes content even when the repo
  advances.
- **severity**: **P1** — contributed to the recovery hazard documented
  in the 2026-04-21 DB-pool incident (platform's vendored tgz missed
  `08ac314` runtime-api memory bump + the secret-ref refactor). Not a
  direct fire, but a systematic detection blind spot.
- **scope shape (groom view; plan assigns DoDs)**:
  - **B.1 — version bump policy + one-shot retro-bump**. Cut
    `Chart.yaml.version` forward to a real number matching the most
    recent repo release tag (`0.10.3` if aligned with `v0.10.3`, or
    `0.11.0` if we treat the backlog of chart changes as a minor
    bump — plan picks). One commit, one file edit.
  - **B.2 — publish via GitHub Pages Helm repo**. Add
    `.github/workflows/chart-release.yml` using
    `helm/chart-releaser-action` (the standard pattern — ~25 YAML
    lines). On push to `main` with a chart-version change, the
    action packages `vexa-<version>.tgz`, pushes it to the
    `gh-pages` branch with an `index.yaml`, and drafts a matching
    GitHub Release. Consumers gain `helm repo add vexa
    https://vexa-ai.github.io/vexa`.
  - **B.3 — pre-commit discipline (scope-optional)**. A one-file
    hook (`tests3/lib/git-hooks/pre-commit-chart-version.sh`) that
    fails commit if `deploy/helm/charts/**` changed without a
    `Chart.yaml.version` bump. Same shape as the other pre-commit
    hooks already under `tests3/lib/git-hooks/`. Plan decides
    whether to bundle or defer — the workflow in B.2 is enough on
    its own; the hook is belt-and-braces.
- **estimated scope**: B.1 ≈ 10 minutes. B.2 ≈ 2 hours (write + one
  test-publish dry-run + docs). B.3 ≈ 30 minutes. **~half a day
  total.**
- **repro confidence**: HIGH — reporter linked the exact commit
  (`bf3dd83`) and the exact chart commits missed by downstream
  (`08ac314` + secret-ref refactor). `helm/chart-releaser-action` has
  a stable public pattern; thousands of OSS charts use it.
- **owner feature(s)**: `infrastructure` (chart) — but the publishing
  workflow is CI-side, so touch `.github/workflows/` + possibly a
  new `deploy/helm/README.md` section documenting the consumer add
  command.
- **new check candidates (plan's job)**:
  - `infrastructure.chart.version-matches-repo-tag` — static check
    that `Chart.yaml.version` matches the most recent `v*` git tag
    on the commit hash (or is one SemVer bump ahead).
  - `infrastructure.chart.publish-workflow-exists` — static
    existence check for `.github/workflows/chart-release.yml` with
    `helm/chart-releaser-action` reference.
  - `infrastructure.chart.artifact-published` (optional, post-ship
    gate) — asserts `https://vexa-ai.github.io/vexa/index.yaml`
    serves a fresh entry for the current version. Probably deferred
    to post-ship verification rather than inline validate.

---

## Suggested cycle shape — human picks

### Shape 1 — Both packs  (**my recommendation**)

- Pack A (A.1 + A.2) — half-day, dogfoods itself this cycle.
- Pack B (B.1 + B.2, defer B.3 unless the plan sees low overhead) —
  half-day, unblocks consumers.

Total ≈ 1 day of work, two reviewers-friendly commits. Both
independently shippable if one gets stuck.

### Shape 2 — Pack A only

- Ship #229 + worktree convention. Defer #228 to a follow-on cycle.
- Justified if the consumer-facing publish workflow is blocked on
  an org-level decision (e.g. "should charts live on OCI instead of
  gh-pages?"). Ask the human: *is there a pending architectural call
  on chart distribution?*

### Shape 3 — Pack B only

- Ship #228. Defer #229 tooling to later.
- Downside: this release does not itself demonstrate the worktree
  pattern, and the next release that tries to `make release-worktree`
  has to bootstrap the tool first. Weaker story.

### Shape 4 — Defer both

- No release. Wait until a fire forces the issue.
- Downside: #228 already has a documented fire-shaped blast radius
  (2026-04-21 incident); deferring lets the same class recur.

---

## Open questions for plan

1. **Pack A owner feature.** `tests3/` has no `features/tests3/dods.yaml`
   today; its "DoDs" are the stage contracts + registry checks. Plan
   decides whether this release warrants creating such a feature folder
   or whether the two new check IDs under Pack A ride as registry-only
   entries.
2. **Pack B version number.** `0.10.3` (align with latest repo tag) vs.
   `0.11.0` (acknowledge the ~100-commit backlog as a minor bump).
   Plan picks after quick spot-check of what changed in the chart
   between `bf3dd83` and HEAD.
3. **Pack B publish path.** `gh-pages` (classic Helm repo) vs. OCI
   (push to `ghcr.io/vexa-ai/vexa`). Reporter suggested gh-pages as
   the common pattern. Plan confirms or flips based on Vexa's
   existing CI footprint.
4. **B.3 bundle or defer?** Pre-commit hook for chart-version
   discipline. Plan weighs reviewer load.

None of these change the shape of either pack — they're parameter
decisions that plan resolves in under a few minutes with a spot-check.

---

## Approvals

Human picks Shape + per-pack approval; AI does not advance. Per
`stages/01-groom.md` Exit: *"human has marked at least one pack with
`approved: true`."*

```yaml
cycle_shape: 1            # 1 (both) | 2 (A only) | 3 (B only) | 4 (defer)

packs:
  A:
    approved: true        # human: "go" (2026-04-22T17:45Z)
    note: ""
  B:
    approved: true        # human: "go" (2026-04-22T17:45Z)
    note: "B.1+B.2 core; B.3 (pre-commit hook) plan decides bundle vs defer"
```

Human also confirmed static-only validate (no provision/deploy of
service image) — plan will flag `deployments.modes: []` and note the
no-code-release shape.

Once any pack is `approved: true`, advance via
`make release-plan ID=260422-release-plumbing` to scaffold
`scope.yaml` + `plan-approval.yaml`.
