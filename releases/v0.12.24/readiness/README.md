# v0.12.24 — readiness receipts

One receipt per leg of [`../readiness.yaml`](../readiness.yaml), named `<leg>.receipt.json`, in the
shape [`release/readiness/readiness.schema.json`](../../../release/readiness/readiness.schema.json)
defines. Bind each to the candidate map it was taken against:

```bash
shasum -a 256 releases/v0.12.24/candidate-images.json   # → candidate_map_sha
node release/readiness/check.mjs --phase formation --release v0.12.24
```

## Status at formation — the manifest exists, no leg can be bound yet

`check.mjs --release v0.12.24` currently stops before the leg table with one line:

```
readiness: no candidate map at releases/v0.12.24/candidate-images.json
  — nothing to bind the readiness receipts to
```

That is the correct answer, not a gap to route around. **Every leg declares
`binds_candidate_map: true`, and every receipt requires a `candidate_map_sha`** — so no receipt for
this release can be written before a candidate exists. The candidate needs merged commits and a
build run; at the time of writing **none of the train's twelve pull requests is merged**, and the
merges are held by founder decisions, not by engineering (see
[`DmitriyG228/biz#435`](https://github.com/DmitriyG228/biz/issues/435)).

Writing the receipts anyway would mean inventing a binding. The v0.12.23 README states the rule this
harness exists for, and it applies to its own operator: *a receipt written ahead of its finding is a
false green.*

| Leg | Receipt | Why not yet |
|---|---|---|
| 1 `train-value` | ⛔ none | No candidate map, and no `v0.12.24` tag: the batch `v0.12.23...v0.12.24` cannot be resolved, so the oracle cannot form a truthful `input_identity` |
| 2 `blast-radius` | ⛔ none | Not run at this formation |
| 3a `full-functionality` | ⛔ none | Staging leg; no candidate bytes to pull |
| 3b `api-docs-sweep` | ⛔ none | Staging leg; nothing deployed to sweep |
| 4 `security-review` | ⛔ none | **The review ran and is final — 🟢 green, no blocking finding.** Two blockers to binding, below |
| 5 `compliance-review` | ⛔ none | **The review ran and is final — 🔴 red.** Most of its punch-list was cleared at formation (below); same two blockers to binding |
| 6 `promotion-ceremony` | ⛔ none | Staging leg; no rehearsal packet for this release |

### Legs 4 and 5 — two separate reasons neither can bind

1. **No candidate map.** As above; and this leg's `input_pattern` is
   `^v\d+\.\d+\.\d+\.\.[0-9a-f]{40}` — a *single* candidate head sha. At formation there are
   **twelve unmerged heads and two different dependency hashes**, so no such sha exists to name.
   The security review recorded this against itself rather than papering over it.
2. **No durable public home for the findings.** A receipt's `findings_ref` must point at a durable
   public artifact. Both reports exist only as drafts in a **private** repository and as comments on
   a private issue. `Vexa-ai/vexa` is public; `DmitriyG228/biz` is not.

**Publishing them is a disclosure decision, not a mechanical step** — it is the founder's call, not
this session's. Either home works when made: issues on `Vexa-ai/vexa`, or
`releases/v0.12.24/readiness/<leg>.md` in this repository.

### What the two reviews are, stated plainly

Both are **human formation artefacts**. `check.mjs` never executes a leg's oracle — it validates
shape and binding only. **A receipt therefore proves that a review was bound to specific bytes; it
never proves that the review happened, or that it was any good.** That is true of every agent leg in
this manifest, and it is the reason the protocols are written down and the reports are readable.

### Findings cleared at formation, before any candidate was cut

The compliance verdict was red on a punch-list, and the punch-list was worked rather than deferred.
Cleared and verified: two pull requests retargeted off a squash-merged base (by rebase — a bare
retarget would have carried 223 files, not 15); two wrong auto-closes killed
([#451](https://github.com/Vexa-ai/vexa/issues/451) and
[#866](https://github.com/Vexa-ai/vexa/issues/866) both stay open, confirmed via
`closingIssuesReferences`); customer material scrubbed out of
[#1093](https://github.com/Vexa-ai/vexa/pull/1093)'s artefacts before they could reach this public
repository; three false claims corrected in pull-request bodies; the `report_issue` credential hole
closed with a red→green pair; `DELETE /recordings/{id}` scoped `BOT` so a read key cannot destroy;
and **`contribution-rights` made a required check** — the structural finding, red on 8 of 11 pull
requests while blocking nothing.

### One finding against this harness itself

The release number appears in **four** places in a manifest: `release`, `candidate_map`,
`receipts_dir`, and the version pinned inside the `train-value` oracle's command. `check.mjs`
guarded the first three and **not the fourth** — a manifest copied forward with a stale
`RELEASE_VERSION=` still ran, against the *previous* release's batch, and reported green. Verified
by mutating the v0.12.23 manifest, which printed
`Oracle: RELEASE_VERSION=v0.12.22 …` under `--release v0.12.23` without complaint. Now guarded, with
a test that fails without the guard.
