# `releases/` — the witness receipts

Each stable release carries **one witness receipt** at `releases/<version>/witness.json` — the
auditable evidence for **guarantee line 7** ("A human witnessed the assembled value. No signature,
no release."). The receipt is the *record*; the *hard gate* is the `release-promote` Environment's
required-reviewer approval — a committed file cannot forge a human approval, so both exist.

## The two-phase release flow (enforced)

Publish and promote are **separate acts**; neither the moving tag `:v012` nor a published GitHub
Release happens before the witness pass is signed.

0. **Version-bump (before you tag)** — advance `appVersion` in
   `deploy/helm/charts/vexa/Chart.yaml`, advance the matching `docs-reflects:` stamp (and the visible
   line) in `docs/docs/changelog.mdx` so `gate:docs-version` stays green, and **assemble the changelog
   fragments** that PRs dropped since the last release (the towncrier pattern —
   [`docs/changelog.d/`](../docs/changelog.d/README.md)):

   ```bash
   node scripts/changelog-collect.mjs --check   # preview pending fragments (exit 3 if any pending)
   node scripts/changelog-collect.mjs           # fold them into the ## <MAJOR>.<MINOR>.x section, remove them
   ```

   The collector appends each `docs/changelog.d/<pr>-<slug>.md` bullet into the current version
   section and deletes the consumed fragments; it does **not** touch the `docs-reflects:` stamp (you
   just advanced it). Commit the bumped `Chart.yaml` + assembled `changelog.mdx` + emptied
   `changelog.d/` together, then tag.

1. **Publish + validate** — push tag `vX.Y.Z`. `release-images` builds and publishes the versioned
   `:vX.Y.Z` images and runs `release-validate` with **`promote: false`** — the L4 legs prove the
   published bytes, but `:v012` does **not** move. The release candidate now exists to be witnessed.

2. **Witness** — on a fresh self-host of the **published** `:vX.Y.Z` images, admit a bot to a real
   meeting, walk every user-visible batch value once, and record what you saw. **Generate the
   witness script FROM THE BATCH** — it lists every PR so no value is missed — then resolve each
   entry and commit to `main`:

   ```bash
   mkdir -p releases/vX.Y.Z
   RELEASE_VERSION=vX.Y.Z GITHUB_REPOSITORY=Vexa-ai/vexa node scripts/release-witness-script.mjs \
     > releases/vX.Y.Z/witness.json
   # fill witnessed_by · witnessed_at · deployment; then RESOLVE every entry in values[]:
   #   user-visible → walk it live, set witnessed:true + observation + pass
   #   backend / ci → witnessed:"by-proxy" with its named evidence (test / leg / gate)
   # set signed_off:true, commit.
   ```

   Every PR merged since the last release is one entry — the generator classifies it (user-visible
   + platform / backend / ci) and auto-names its machine evidence. Classification is best-effort;
   downgrade an over-marked user-visible entry to `by-proxy` (with its evidence) or walk it — either
   is a conscious decision, which is the point: **no value is silently skipped.**

   **"Since the last release" means the last one that SHIPPED, not the last tag.** A tag is not a
   release: a candidate can be tagged, published, fail its witness, and be abandoned. The baseline
   is therefore the greatest lower tag carrying a `releases/<tag>/witness.json` — an abandoned
   candidate has no receipt, so its PRs stay in the batch and are witnessed by the release that
   actually delivers them. The generator logs the baseline it picked and any candidate it skipped.
   (v0.12.5 was tagged, published, witness-failed, abandoned; v0.12.6's batch is therefore
   `v0.12.4...v0.12.6` — the 15 PRs a `:v012` user actually receives — not the 2 since the tag.)
   `RELEASE_PREV_TAG=vA.B.C` overrides the baseline; it is an escape hatch and is logged loudly.

   The rule to hold onto: **the batch is what the promote hands users** — `(last shipped) → (this
   version)`. If those diverge, the receipt is describing a release that isn't the one shipping.

3. **Promote** — dispatch `release-validate` with `promote: true`. Two gates run first:
   - **`value-gate`** (guarantee 8) — every batch PR is `pr-value`-green on its head or `state: value-signed`.
   - **`witness-gate`** (guarantee 7) — `releases/vX.Y.Z/witness.json` is present, well-formed, version-matched.

   Then the `promote` job pauses on the **`release-promote` Environment** for the owner's approval.
   On approval, `:v012` moves. `release-published-guard` re-checks both gates on the published
   GitHub Release and **retracts it to draft** if either is unmet.

## Receipt schema (`witness.json`)

```json
{
  "version": "vX.Y.Z",
  "candidate": "vX.Y.Z",
  "generated_from": "v<prev>...vX.Y.Z",
  "witnessed_by": "who ran the pass",
  "witnessed_at": "YYYY-MM-DD",
  "deployment": "compose | lite | helm",
  "values": [
    { "pr": "599", "title": "MS Teams self-evict fix", "visibility": "user-visible",
      "platform": "ms-teams", "witnessed": true,
      "pass": "bot admitted, stays active >2min, never self-evicts",
      "observation": "joined my Teams meeting, stayed 4min, transcript rendered" },
    { "pr": "601", "title": "gateway auth pool isolation", "visibility": "backend",
      "witnessed": "by-proxy",
      "evidence": "test_adapters_resolve.py::test_build_wires_separate_pools + test_proxy.py::test_auth_infra_failure_is_503" }
  ],
  "signed_off": true
}
```

`version`/`candidate` must equal the release; `witnessed_by`/`_at`/`deployment` non-empty;
`signed_off:true`; and **every** entry in `values[]` must be resolved — a user-visible one with
`witnessed:true` + `observation` + `pass`, a backend/ci one `by-proxy` with named `evidence`. The
gate ([`scripts/release-witness-gate.mjs`](../scripts/release-witness-gate.mjs)) fails on any
unresolved entry, so the batch is fully accounted for. See
[the delivery constitution](../docs/docs/governance/delivery.mdx) (ship bar, the guarantee),
[ADR-0029](../docs/adr/0029-release-witness-and-value-gates-enforced.md), and
[ADR-0031](../docs/adr/0031-witness-script-generated-from-the-batch.md).
