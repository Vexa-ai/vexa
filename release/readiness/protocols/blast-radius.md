# Blast-radius protocol

Proves that every change riding the candidate has been mapped from its actual diff to the runtime
surfaces it touches, and that each surface is either covered by a check that exists and ran on the
shipped path, or **named as uncovered**.

**Leg id:** `blast-radius` · **fires:** `formation` · **re-run:** on every candidate re-cut.

## Inputs

| Input | How to obtain |
|---|---|
| Baseline tag (last **shipped** release) | greatest lower tag carrying `releases/<tag>/witness.json` — a tagged-but-abandoned candidate is not a baseline |
| Branch point | `git merge-base <baseline-tag> <candidate-head>` |
| Candidate head sha | `git rev-parse <candidate-branch>` — full 40 chars |
| Diff census | `git diff --stat <baseline-tag>..<head>` · `git log --oneline <baseline-tag>..<head> \| wc -l` |
| Stated PR/issue roster | the release scope list you were handed — treat as a **claim**, not a fact |
| Real merge roster | `git log --merges --oneline <baseline-tag>..<head>` |
| PR state per item | `gh pr view <N> --json state,mergedAt,mergeCommit,baseRefName,headRefOid` |
| Candidate map | `releases/<version>/candidate-images.json` — image classes (`prod_deployed` / `oss_only`) |
| Workflow runs | `gh run list --workflow release-images.yml` · `gh run view <id> --json jobs` |
| Sealed contract | `core/gateway/contracts/api.v1/` at the candidate |

If the baseline tag and the branch point differ, diff them: `git diff --stat <baseline>..<branch-point>`.
Commits that touch only release notes, witness or scripts carry nothing runtime, so the range is safe
to treat as the train. Any runtime file there and the baseline is wrong.

## Input identity

The exact diff range — baseline tag, head at full 40 chars, nothing else (the shape
`releases/<version>/readiness.yaml` pins for this leg):

```
v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307
```

```bash
printf '%s..%s\n' "$PREV_TAG" "$(git rev-parse "$HEAD")"
```

A new commit on the candidate branch changes this string and voids the receipt. Record the baseline's
resolved sha and the branch point in the findings, not in the identity.

## Method

1. **Fix the baseline, then the range.** Per the two-phase rule in `releases/README.md`, the batch is
   *(last shipped) → (this version)*. Record commit count, file count and `+/−` lines.

2. **Correct the roster before you analyse it.** Four checks per claimed item, each with a command:
   - PR or issue? (`gh pr view` vs `gh issue view` — an issue number in a roster is a common defect)
   - merged, or **closed-unmerged with a hand-merge**? (`mergedAt: null` + code present)
   - is its merge commit an ancestor of the head? `git merge-base --is-ancestor <merge> <head>`
   - what base did it merge into? A PR merged into the train branch, not `main`, fires no `Closes`.

   Then invert it: `git log --merges <range>` and subtract the roster. **Anything in the train and
   absent from the roster is the highest-priority row** — in the v0.12.23-rc.18 run the train's
   highest-radius single change (a live-WS envelope swap) was not on the list being reviewed.

3. **Decompose a mega-PR.** Any PR that is a large fraction of the diff and whose commit ranges have
   genuinely different radii is split into lettered blocks (`a`–`e`), each with its own row and its
   own commit range. One row per radius, not one row per PR number.

4. **Map diff → surface, never PR description → surface.** For each row, derive five columns from the
   file list alone:

   | Column | Derived from | Rule |
   |---|---|---|
   | **Images** | which release Dockerfile copies the changed path | name the `candidate-images.json` key **and its class**; `oss_only` is not deployed to prod |
   | **Routes** | router modules | list ADDED and CHANGED separately; a status-code change (`200`→`404`, new `409`) is a CHANGED route |
   | **Data** | models, migrations, JSONB writers | name table and column. *"JSONB, no DDL, no migration"* is a finding, not an absence — it means rollback and mixed-version reads are untested |
   | **Contract** | `core/gateway/contracts/api.v1/` | a zero-diff contract against N new routes is an uncovered cell, not a pass |
   | **Wire / env** | message envelopes, config keys | name the **consumer**. A consumer outside the candidate map is an R-class risk on its own |

5. **Establish what CI actually ran — read the runs, not the receipts.** For the candidate:
   - Did the build run *conclude failure*? Which legs were `skipped` as a result? Those legs never ran.
   - Is the green evidence a `workflow_dispatch` that **does not contain** the skipped legs?
   - Which workflow holds the unit suites, and did it run on the exact head?
   - Which tree was validated, and how does it differ from the head?
   - Per-PR check state across all train PRs — a check that fails on every one is a finding
     ([the rights-gate parse defect](https://github.com/Vexa-ai/vexa/pull/1095) surfaced exactly this way).

6. **Fill the evidence column only with checks that exist and ran on the shipped path.** Name the file,
   the workflow leg, or the dated live probe. "The suite is green" is not an entry.

7. **Name the UNCOVERED cells.** A surface is uncovered when its only evidence is disqualified:

   | Disqualifier | Why |
   |---|---|
   | fakes-only suite standing in for a DB constraint, advisory lock, or wall-clock behaviour | a partial unique index has no fake analogue |
   | a suite that executes in **zero** workflows | `.gateignore`, absent from the workspace, or not a value-gate runtime prefix |
   | a check that ran on a different tree than the one shipping | name the tree it ran on |
   | an ad-hoc live probe with no committed script | real evidence, **not a regression barrier** — nobody, including us, can re-run it |
   | a test asserting a property one layer above where it must hold | one line moving downstream reopens it silently |
   | the production consumer of a changed contract sitting outside the candidate | nothing crosses that line |

   Write the cell as a sentence naming the surface and what would have to be true. Never "needs more
   testing".

8. **Rank the uncovered cells R1…Rn by production risk**, each with a one-line statement of what breaks
   and when. R1 is whatever breaks the deploy itself. Collapse cells that are the same defect wearing
   different clothes and say so.

9. **Write the negative space.** A short section naming what the evidence genuinely does prove, with the
   best-evidenced change in the train called out as the standard the rest is read against — in rc.18
   that was a live reprobe in which *each guard was proven load-bearing by neutering it*.

10. **Publish findings, then the receipt.** Findings go to a GitHub issue or
    `releases/<version>/readiness/blast-radius.md`. Read-only leg: no commits, comments or pushes to the
    repositories under review.

## Receipt

`releases/v0.12.23/readiness/blast-radius.receipt.json`:

```json
{
  "schema_version": 1,
  "leg": "blast-radius",
  "candidate_map_sha": "3422d02f02985ab0f02fc47f58a6b4b3e23f0163397f1073c004fe44624d764f",
  "input_identity": "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307",
  "result": "red",
  "findings_ref": "https://github.com/Vexa-ai/vexa/issues/1179",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session · blast-radius)"
}
```

```bash
V=v0.12.23; mkdir -p releases/$V/readiness
MAP_SHA=$(shasum -a 256 releases/$V/candidate-images.json | cut -d' ' -f1)
jq -n --arg s "$MAP_SHA" --arg i "$RANGE" --arg r red --arg f "$FINDINGS_URL" \
  '{schema_version:1,leg:"blast-radius",candidate_map_sha:$s,input_identity:$i,result:$r,
    findings_ref:$f,generated_at:(now|todate),generated_by:"agent (readiness session)"}' \
  > releases/$V/readiness/blast-radius.receipt.json
node release/readiness/check.mjs --phase formation --release $V
```

Re-cut the candidate and `candidate-images.json` changes, its sha changes, and this receipt is
`stale`. Re-run the leg against the new range; do not re-stamp the sha.

## Blocking-verdict discipline

`green` requires every row's radius mapped **and** every uncovered cell either non-blocking or waived.

**RED — blocks the train:**
- A `prod_deployed` image carries a guarantee the release claims, and that guarantee's only evidence is
  disqualified by the table in step 7.
- A deploy precondition exists (manual DDL, an index prod lacks, an env var no chart pins) with **no
  pre-flight assertion**. A human reading a runbook is not a gate.
- The candidate build run failed and the green evidence came from a run missing those legs — red until
  the legs run or the candidate is re-cut.
- A wire or status-code contract changed and its production consumer is outside the candidate with no
  compat leg.
- The roster was wrong in a way that left a change unreviewed — re-run the leg; do not patch the table.

**Rides to the next train:** uncovered cells on `oss_only` images; second-order gaps with a named owner
and a filed issue; missing regression barriers for evidence that did run (file the "make it repeatable"
issue and rank it).

**Waivers:** founder only, one per cell, recorded as a `waivers[]` array in the receipt carrying the
cell id, the issue link, and the release it expires in. `result` stays `red` until the waiver exists —
"founder decision needed" is not `green`.

## Failure modes

- **Reviewing the roster instead of the diff.** rc.18's roster claimed a dashboard PR that is not an
  ancestor of the head at all, and omitted the highest-radius change in the train.
- **Counting a closed-unmerged PR as delivered.** Two PRs were CLOSED with `mergedAt: null` while their
  branches were hand-merged — the code ships and the PR record reads "rejected".
- **Taking any green run as the candidate's run.** The failed build skipped five delta/receipt legs; the
  green evidence was a standalone dispatch that does not contain them.
- **Reading suite size as coverage.** 68 files and 847 test functions proved nothing about a partial
  unique index, an advisory lock, or backoff across row recreation — the suite is 100% fakes.
- **Recording an ad-hoc live probe as a covering check.** It is evidence for *this* candidate and a
  barrier for none.
- **A receipt bound to a stale candidate map** after a re-cut — the leg reads green while describing a
  release that is not shipping.
