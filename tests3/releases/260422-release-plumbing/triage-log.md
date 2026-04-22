# Triage log — 260422-release-plumbing

| field       | value                                    |
|-------------|------------------------------------------|
| release_id  | `260422-release-plumbing`                 |
| stage       | `triage`                                  |
| entered_at  | 2026-04-22T18:25Z (approx)                |
| entered_from| `human`                                   |
| reason      | Human-review Part A surfaced a gap in the chart-version policy choice. |

---

## Round 1 — chart-version policy

### Signal

Human turn during code-review (Part A):

> "`0.10.4` may conflict with future `v0.10.4` repo tag semantics.
>  make sure we do not have this question, inherit from the current
>  last release instead"

Referring to Risk note #2 in `code-review.md`: commit `5c5bff6` bumped
`deploy/helm/charts/vexa/Chart.yaml` from `0.1.0` to `0.10.4` —
one-patch-ahead of the latest repo tag `v0.10.3` to capture chart
commits since (e.g. `08ac314` runtime-api memory bump).

### Classification

**Gap, not regression.** All 5 scope-bound checks pass at gate
(`CHART_VERSION_CURRENT` with the `>=` predicate accepts 0.10.4 ≥
0.10.3). No existing DoD would have caught this; no prior commit
regressed. The gap is in the *policy choice* itself — plan settled on
"one-ahead" (per reporter's "bump per chart-touching commit" ask);
human review settled on "inherit from latest tag" (removes future-tag
conflict question).

Not a #228-reporter-repro issue. Plan-internal decision that got
reconsidered during human review.

### Fix target

1. `deploy/helm/charts/vexa/Chart.yaml`
   `version: 0.10.4 → 0.10.3`
   (appVersion already `"0.10.3"` — aligned.)

2. `tests3/checks/scripts/chart-version-current.sh`
   Change predicate from `SemVer >=` to string equality.
   New policy: chart version *equals* latest `v*` tag. Drift in either
   direction (ahead or behind) fails loud.
   Drop the `semver_ge` helper (no longer needed).

3. No scope.yaml changes — the `chart-version-current-per-commit`
   issue's hypothesis text still holds ("reads Chart.yaml.version and
   compares against latest v* tag"). The comparison operator is an
   implementation detail of the check, not the issue hypothesis.

4. No registry.yaml/registry.json entry changes — check ID + dispatch
   + script path all remain identical.

### Consequence for chart-touching commits since v0.10.3

The `08ac314` runtime-api memory bump (+ any other chart commits
between `v0.10.3` and HEAD) are now queued for whoever cuts `v0.10.4`.
That commit:
  (a) cuts `v0.10.4` tag,
  (b) bumps `Chart.yaml.version` to `0.10.4` in the same commit,
  (c) the `chart-release.yml` workflow fires on push-to-main with the
      bumped version, publishes `vexa-0.10.4.tgz` to gh-pages.

Inheritance property restored.

### Next

Transition `triage → develop`, land the fix, redeploy, revalidate,
return to `human` with updated `code-review.md`.

---

## Round 2 — worktree.sh base-branch default

### Signal

During PR preparation, `gh pr create --base dev` failed with "No
commits between dev and release/260422-release-plumbing" — because the
remote no longer has a `dev` branch (repo has standardized on `main`
as the only long-lived integration branch + per-release feature
branches). Checking `git log origin/main..HEAD` revealed the PR would
include **16 commits**, of which 9 belong to the in-flight
`260422-zoom-sdk` release (not shipped; on its own
`origin/release/260422-zoom-sdk` branch).

User: *"stop, zoom should not be there - zoom is unfinished"*

### Classification

**Gap, not regression.** Similar in shape to round 1: a policy default
in our new tooling that looks right locally but is wrong under the
"releases run in parallel from one clone" invariant. The worktree was
bootstrapped with `base=dev` (local dev still had unshipped zoom-sdk
commits), so all of zoom-sdk rides along.

### Fix target

1. `tests3/lib/worktree.sh`: `local base="${2:-dev}"` → `"${2:-main}"`.
   Release worktrees branch off last-shipped state by default. Other
   in-flight releases are isolated on their own worktrees, each
   branched off `main`, with zero coupling between them. Matches
   Pack A.2's design intent ("N-parallel releases from one clone")
   literally.
2. `tests3/releases/260422-release-plumbing/scope.yaml`:
   `tests3-worktree-bootstrap` hypothesis text: `../vexa-<id>` on
   branch `release/<id>` from `dev` → from `main`.
3. Rebase `release/260422-release-plumbing` onto `origin/main` to
   drop the 9 zoom-sdk commits from this release's base. All 7
   release-plumbing commits get new SHAs; `scope.yaml.fix_commits`
   must be updated post-rebase.
4. `git push --force-with-lease origin release/260422-release-plumbing`
   — safe (feature branch; no one else on it; lease prevents
   overwriting any concurrent push).

### Consequence for the release-plumbing invariant story

This triage round is a meta-success: the release that SHIPS the
worktree convention had its OWN worktree built with the wrong default,
and we found it exactly when the invariant demanded ("isolated
releases"). The fix is the lowest-possible blast-radius one-word
change (`dev` → `main`) + the rebase. If this class of gap stays
latent, every future release using `release-worktree ID=<id>` would
silently inherit in-flight other-release commits — exactly the
class-of-bug #229 exists to prevent.

