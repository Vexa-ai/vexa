# `releases/` — the witness receipts

Each stable release carries **one witness receipt** at `releases/<version>/witness.json` — the
auditable evidence for **guarantee line 7** ("A human witnessed the assembled value. No signature,
no release."). The receipt is the *record*; the *hard gate* is the `release-promote` Environment's
required-reviewer approval — a committed file cannot forge a human approval, so both exist.

## The two-phase release flow (enforced)

Publish and promote are **separate acts**; neither the moving tag `:v012` nor a published GitHub
Release happens before the witness pass is signed.

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
