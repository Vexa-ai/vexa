# `releases/` — the witness receipts

Each stable release carries **one witness receipt** at `releases/<version>/witness.json` — the
auditable evidence for **guarantee line 7** ("A human witnessed the assembled value. No signature,
no release."). The receipt is the *record*; the *hard gate* is the `release-promote` Environment's
required-reviewer approval — a committed file cannot forge a human approval, so both exist.

## The two-phase release flow (enforced)

Publish and promote are **separate acts**; neither the moving tag `:v012` nor a published GitHub
Release happens before the witness pass is signed.

0. **Version-bump (before you tag)** — advance **all three version stamps** `release-images`
   preflight cross-checks: `version` in the root `package.json`, `appVersion` in
   `deploy/helm/charts/vexa/Chart.yaml`, and the matching `docs-reflects:` stamp (and the visible
   line) in `docs/docs/changelog.mdx` so `gate:docs-version` stays green. Then **assemble the
   changelog fragments** that PRs dropped since the last release (the towncrier pattern —
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

   That run proves the published manifests resolve with their required platforms and that every
   validation leg passes on the published bytes. It does **not** prove the *packet-bound
   candidate-map identity*, because the map records the digests this build has only just
   published — at freeze time the identity is not unreviewed, it is unknowable. The run says so:
   a `candidate-map identity DEFERRED` notice and a ⚠️ in guarantee line 1. Step 1a closes it.

### 1a. Bind, validate, freeze — the human step (load-bearing, once per candidate)

Nothing automates this and nothing can: pinning a map's sha256 into the workflow **is** the
review. Until it is done, the candidate has no reviewed identity — which is exactly what the
promote and stable-tag gates refuse to move on.

1. **Bind the packet.** Write `releases/v<BASE>/candidate-images.json` from the *exact published*
   registry identities of the eleven images (11 top descriptors, 21 platform identities — the bot is
   amd64-only), then pin its hash in **both** places that assert it:
   - the reviewed-identity table in the `resolve` job of
     [`.github/workflows/release-validate.yml`](../.github/workflows/release-validate.yml)
     (`CANDIDATE_MAP` / `EXPECTED_MAP_STABLE_TAG` / `EXPECTED_MAP_SHA256` / the two counts), and
   - the canonical-packet assertion in
     [`release/candidate-image-map.test.mjs`](../release/candidate-image-map.test.mjs).

   ```bash
   REF=vexaai/vexa-lite:vX.Y.Z-rc.N
   docker buildx imagetools inspect "$REF" --format '{{json .}}' | jq '.manifest'   # top + platforms
   docker manifest inspect -v "$REF" | jq -r '.[].OCIManifest.config.digest'        # image configs
   node release/candidate-image-map.mjs check releases/vX.Y.Z/candidate-images.json vX.Y.Z
   shasum -a 256 releases/vX.Y.Z/candidate-images.json     # → EXPECTED_MAP_SHA256
   ```

   Commit as `release: bind <candidate> train-candidate packet`.

2. **Validate.** Dispatch `release-validate` standalone **from that branch** with
   `version: vX.Y.Z-rc.N`, `promote: false`. The identity check now enforces (the arm exists), and
   the legs re-run in minutes against the already-published images — no rebuild.

3. **Freeze.** Write the run's `validation_source` / `validation_run` back into the map, re-hash,
   and re-pin the new `EXPECTED_MAP_SHA256` in the same two places (writing the fields changes the
   map, so the hash changes with them). Commit as
   `release: freeze <candidate> validation identity (run <id>, all legs green)`.

**Where the identity check stays fail-closed** — a deferral is only ever the freeze window:
any `promote: true` dispatch, any stable (non-prerelease) version, and any version a committed
map already names all still refuse to run without a reviewed, pinned identity. `promote` carries
its own independent stable-promotion map table on top of that, and `release-images`'
`alias-candidate` job proves every digest against the committed map before a stable tag is
written. See [`Vexa-ai/vexa#1237`](https://github.com/Vexa-ai/vexa/issues/1237).

2. **Witness** — the human walks a **DELIVERED deployment** (D-L4): the agent provisions a fresh
   self-host of the **published** `:vX.Y.Z` images, pre-validates it autonomously (health, UI,
   auth path, STT readiness), and hands the human a **running UI URL — never a setup recipe**.
   The delivered deployment is recorded in the receipt (`witness_deployment: {url,
   provisioned_by, prevalidated[]}`) and the witness gate refuses a receipt without it. The human
   then admits a bot to a real meeting, walks every user-visible batch value once, and records
   what they saw. **Generate the witness script FROM THE BATCH** — it lists every PR so no value
   is missed — then resolve each entry and commit to `main`:

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
   On approval, `:v012` moves.

4. **Publish the Release** — after `:v012` moves, a human cuts the GitHub Release:

   ```bash
   gh release create vX.Y.Z --verify-tag --latest --notes-file <notes>
   ```

   The notes are assembled from the batch's value list and the witness receipt (v0.12.10's Release
   notes are the shape reference). `release-published-guard` re-checks both gates on the published
   GitHub Release and **retracts it to draft** if either is unmet. (This documents the current
   *manual* flow that #582 will mechanize — whichever lands second reconciles this text.)

## Receipt schema (`witness.json`)

```json
{
  "version": "vX.Y.Z",
  "candidate": "vX.Y.Z",
  "generated_from": "v<prev>...vX.Y.Z",
  "witnessed_by": "who ran the pass",
  "witnessed_at": "YYYY-MM-DD",
  "deployment": "compose | lite | helm",
  "witness_deployment": { "url": "http://<host>:3000", "provisioned_by": "agent (throwaway VM <id>)",
    "prevalidated": ["gateway /health 200", "terminal UI 200", "debug login ok", "STT ready"] },
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
