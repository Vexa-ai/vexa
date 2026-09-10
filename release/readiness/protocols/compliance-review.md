# Compliance review protocol

Proves that the candidate keeps the promises made outside the code — the published legal and docs
pages, the sealed architecture, the delivery constitution, and the custody record — and names every
place where a promise and the shipping behaviour disagree.

**Leg id:** `compliance-review` · **fires:** `both` · **re-run:** on every candidate re-cut and on any
change to the roster (a merge, a close, a label).

Scope is a founder ruling in four parts — **legal/privacy**, **architecture**, **principles**, **delivery
process** — and all four run. An engineering audit, not legal advice: it names gaps so counsel can weigh
them.

## Inputs

| Input | How to obtain |
|---|---|
| Diff range | the range the blast-radius leg fixed |
| Merge state of the head | `git merge-base --is-ancestor <head> origin/main` — state the rung it establishes |
| Roster snapshot | `gh pr list --state all --search 'base:<train-branch>' --json number,state,mergedAt,mergeCommit,headRefOid,author` |
| Check-runs per merged head | `gh api repos/Vexa-ai/vexa/commits/<headRefOid>/check-runs` |
| Issue orbit | `gh issue view <N> --json body,labels,state` for every issue the train touches |
| Published pages | fetched live, dated in the findings: terms, DPA, privacy, `docs.vexa.ai/security-compliance`, the how-to pages a user reads while arming the feature |
| Gate suite at the candidate | a **clean detached checkout** of `<head>`; `git status --porcelain` empty before and after |
| Witness receipt | `releases/<version>/witness.json` + `scripts/release-witness-gate.mjs` |
| Changelog fragments | `git ls-tree --name-only <head> docs/changelog.d/` |
| Identity chain | map sha vs the pin in `release/candidate-image-map.test.mjs` vs `EXPECTED_MAP_SHA256` in `.github/workflows/release-validate.yml` |

Every file claim is re-verified at the candidate — `git show <head>:<path>` or the detached checkout.

## Input identity

The reviewed diff plus the roster it was checked against — the shape
`releases/<version>/readiness.yaml` pins for this leg:

```
v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307 + roster@9c41ab30e7f2b5d4
```

```bash
ROSTER=$(gh pr list --state all --search "base:$TRAIN_BRANCH" \
          --json number,state,mergedAt,mergeCommit,headRefOid | shasum -a 256 | cut -c1-16)
printf '%s..%s + roster@%s\n' "$PREV_TAG" "$(git rev-parse "$HEAD")" "$ROSTER"
```

A PR merging, closing or relabelling changes the roster sha and voids the receipt — deliberately, since
half this leg's findings are custody findings.

## Method

### A · Legal and privacy

**A1 — recording consent and disclosure.** Establish what changed in *who gets recorded*, not in how;
verify the before-state on a deployed revision rather than by reading. Build the disclosure table — one
row per channel with `path:line` evidence: display name · platform-native recording indicator ·
in-meeting chat announcement · spoken announcement · acknowledgment-before-capture — and check whether
the *name itself* signals recording or may be set to a plausible human name. Then the published-documents
table: each page, its published statement, and **true after this train?**, fetched live and dated.
Finally name the strictest markets with live accounts or active deals, statutes in shape only, for counsel.

**A2 — new personal-data classes.** Enumerate every field the train newly persists: exact column and JSONB
key path, whether it is indexed, whether it rides an API response, and whether any of it is a **credential**
(a secret feed address is a bearer credential granting continuing read). Credit the redactions that hold,
naming the projection function. A severity label written for performance reasons ("row inflation") is
re-read as a data-minimisation matter and re-triaged.

**A3 — retention.** Search for any automated deletion: age sweeper, `retention_days`, object-storage
lifecycle policy. Record the delta against the published retention promise as one sentence of the form
*published: X · implemented: Y*.

**A4 — erasure.** Does a user-deletion path exist at all? Then the FK graph — for each relevant foreign
key, its `ON DELETE` and the consequence — because cascade-based erasure is unavailable if any edge is
missing or RESTRICT. Then list what **this train adds to the erasure surface**, item by item, flagging
anything inside a JSONB blob with no foreign key (an ordered-deletion plan does not reach it).

**A5 — non-production copies.** Does the prod→staging scrub reach the new data classes? Read the script,
its table filter and its transform, and distinguish a **credential scrub** (things that can act) from a
**PII scrub** (things that can be read) — say which one exists. Verify against the branch the candidate
actually staged from, not against `main`.

**A6 — the pattern check.** List every privacy correction that is *written but unmerged* and that this
train makes load-bearing, with commit, branch and issue — three existed in the rc.18 run. Shipping the
feature while its mitigations sit on branches is the one sequencing decision that is genuinely one-way.

### B · Architecture

**B1 — seals move legally.** `contracts.seal.json` and `architecture.seal.json`: unchanged, or changed
**atomically in one commit** together with the model and both generated projections. A seal moving in a
commit that does not regenerate its projections is drift.

**B2 — run the gate suite read-only** on a clean detached checkout of the candidate; record each line
verbatim (dataflow counts, schema conformance, contract-version freeze, isolation, graph, exports,
config-contract, db-schema/budget) and state which gates were **not** run — anything that builds images
or starts containers — rather than implying a full pass.

**B3 — no illegal shared writers.** For each carrier the train touches, confirm the declared writer set
did not grow and that a new component reads over a declared edge rather than writing a store it does not
own. Then check the gate *can see* multi-writer carriers at all: in the rc.18 run two declared ones never
surfaced because the reporting loop short-circuited before the shared-writer push. A gate that cannot
report is not evidence.

**B4 — no contract widening without a version bump.** Enumerate every new public route, request parameter,
response field and changed status code, and compare against the sealed contract's `info.version` and hash.
Implementation ahead of contract is a gap — and check **why no gate caught it**: a conformance check scoped
to a hard-coded allowlist filters new routes out of its own comparison and is green by construction. Name
the structural hole as well as the instance, or it regresses next release.

**B5 — allowlists and known-gaps.** Isolation config, allowed-edges tables and known-gaps ledgers must be
untouched, or each growth justified in the same commit as the change that needed it.

### C · Principles

**C1 — value-sentence discipline (D5 / D5b).** Every issue the train delivers carries the mandated
`### Value this issue delivers` heading, one witnessable sentence per value, first. Audit the **issues**,
not the PRs — auditing a PR for a value section is a category error; find the owning issue. Release notes
derive from these sentences, so any backfill is due before the notes are cut.

**C2 — fixture discipline.** Fixtures must exercise the artefact the repo actually produces: a rights-gate
fixture that put a marker on the same line as its checkbox — a shape the template never emits — won for
months while the gate failed on every real PR. Also verify doctrine pointers resolve; a referenced file
that does not exist is worse than no reference, since every agent told to read it first finds nothing.

**C3 — one-loop-one-writer on the release machinery.** Grep every workflow at the candidate for
`git commit` / `git push` / `create-pull-request`. Where a file genuinely has two participants, require a
**named split** stated in the script itself. Flag hash-comparison hazards: a sha hand-maintained in three
places is three writers on one value, and it has gone stale before.

### D · Delivery process

**D1 — witness receipt schema conformance.** Run the receipt against its own gate rather than reading it.
Field names must be the gate's field names — the v0.12.23-rc.18 receipt wrote `pass_criterion` where the
gate reads `pass`, so a substantively complete receipt could not pass. Confirm the founder-only field is
unset by a human's choice rather than by omission.

**D2 — issue custody.** Per PR: a closing reference, or a bare mention (a mention creates no closure)?
Any PR **closed-unmerged while its code ships** via hand-merge — the receipt and GitHub then disagree
about what happened. Do lifecycle labels match reality? If nothing is closed, establish the mechanism
(PRs based on a train branch fire no `Closes` until the umbrella merges) — a mechanism is a different
finding from neglect.

**D3 — DCO sign-off.** `git log --format='%H %(trailers:key=Signed-off-by)' <range>`: merge commits are
exempt, content commits are not. Then external contributions inside squashed maintainer PRs — check
`Co-authored-by` trailers, since a rights gate keyed to the *PR author's* checkbox never reaches them,
and a corporate contributor domain is the signal the employer-authorization path exists to catch.

**D4 — contribution rights.** Read the check-runs on each merged PR's head sha. A merge past a red
required governance check is a governance breach, reported with the mechanism distinguished: a body
**clobbered** by a later edit versus one **created** without the template section (`userContentEdits`
returning zero edits means it was never there). Confirm the branch ruleset requires the check with an
empty bypass list, or record that you could not read the ruleset and why.

**D5 — changelog fragments vs merged PRs.** Do not judge user-visibility yourself: use the witness
receipt's own `visibility` stamps as the denominator and count fragments against it. A row the receipt
calls user-visible with no fragment is a contradiction between two of our own artefacts.

**D6 — identity chain.** Verify the candidate-map sha agrees across all three copies — map content, test
pin, workflow arm — **at every rc in the series**, by hashing the blob at each commit; a break since
repaired is still reported with the commits that broke and fixed it. Then check `build_source` ancestry:
built from a commit that is not an ancestor of the tag means the published tag's history will not contain
the commit its images came from. Confirm that topology is intended.

## Receipt

`releases/v0.12.23/readiness/compliance-review.receipt.json`:

```json
{
  "schema_version": 1,
  "leg": "compliance-review",
  "candidate_map_sha": "3422d02f02985ab0f02fc47f58a6b4b3e23f0163397f1073c004fe44624d764f",
  "input_identity": "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307 + roster@9c41ab30e7f2b5d4",
  "result": "red",
  "findings_ref": "https://github.com/Vexa-ai/vexa/issues/1179",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session · compliance-review)"
}
```

```bash
V=v0.12.23; mkdir -p releases/$V/readiness
MAP_SHA=$(shasum -a 256 releases/$V/candidate-images.json | cut -d' ' -f1)
jq -n --arg s "$MAP_SHA" --arg i "$IDENTITY" --arg r red --arg f "$FINDINGS_URL" \
  '{schema_version:1,leg:"compliance-review",candidate_map_sha:$s,input_identity:$i,result:$r,
    findings_ref:$f,generated_at:(now|todate),generated_by:"agent (readiness session)"}' \
  > releases/$V/readiness/compliance-review.receipt.json
node release/readiness/check.mjs --phase staging --release $V
```

A re-cut voids the receipt; so does a roster change, which is why the roster sha is in the identity.

## Blocking-verdict discipline

`green` requires all four sub-parts run and no blocking gap open. **"Founder decision needed" is `red`,
not `green`** — an undecided decision is an open gap, and the decision is what closes it.

**RED — blocks the train:**
- A published legal, security or docs page states something the candidate makes false, or false for a
  materially larger population. A live public claim is not a soft gap.
- A privacy mitigation this train makes load-bearing exists only as an unmerged branch.
- The witness receipt cannot pass its own gate.
- A merge past a red required governance check, until retro-attested (surgically — never overwrite a body
  carrying a human attestation) or until the ruleset is confirmed and recorded.
- A sealed contract moved without its projections, or a sealed route's status/shape changed under the
  frozen version.
- A non-production copy path that clones data the scrub does not reach, while that path is still armed.

**Rides to the next train, with a filed issue and a due date:** additive unsealed routes, documented and
with a `lane:contract` PR queued — that blocks OSS publication, not the deploy. Missing value sentences
and changelog fragments, which block the **release-notes cut**. Third-party-notice disclosure gaps. Stale
ADR text.

**Waivers:** founder only, one per gap, recorded in the receipt's `waivers[]` with the gap id, the issue
link and the release it expires in. Sequencing decisions ("ship before the legal-pages patch merges")
are waivers and are recorded as such, not as green.

## Failure modes

- **Reading files from the working tree.** A tree sitting on a pre-train branch returns pre-train content;
  in the rc.18 run that would have inverted the rights-gate finding entirely.
- **Auditing PRs for value sections.** PRs are not the atom of value; find the owning issue.
- **Reading a skipped required-shaped check as satisfied.** "All legs green" described a run with three
  skipped legs, two of them the value and witness gates. Report `N green / M skipped` and name the skipped.
- **Trusting a green gate that cannot fail.** An allowlist-scoped conformance check filters new routes out
  of its own comparison; twelve new public routes could never turn it red.
- **Counting a written fix as shipped.** A commit on a branch mitigates nothing.
- **Treating a repaired identity-chain break as historical** — report it with the commits, since the
  mechanism that allowed it is still there.
