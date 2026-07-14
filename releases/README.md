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
   meeting, speak, confirm the live transcript renders, and walk every user-visible batch value once.
   Scaffold the receipt from the batch, fill it, and commit it to `main`:

   ```bash
   mkdir -p releases/vX.Y.Z
   GITHUB_REPOSITORY=Vexa-ai/vexa node scripts/release-witness-template.mjs vX.Y.Z v<prev> \
     > releases/vX.Y.Z/witness.json
   # fill witnessed_by · witnessed_at · deployment · evidence.* · prune values_walked · signed_off:true
   ```

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
  "witnessed_by": "who ran the pass",
  "witnessed_at": "YYYY-MM-DD",
  "deployment": "compose | lite | helm",
  "evidence": {
    "meeting_url": "the real meeting the bot joined",
    "transcript": "proof it rendered — segment ids / link / screenshot",
    "live_stream": "confirmed",
    "values_walked": ["#NNN each user-visible batch value, experienced once"]
  },
  "signed_off": true
}
```

Every field is required; `candidate`/`version` must equal the release; `live_stream` must be
`"confirmed"`; `values_walked` must be non-empty; `signed_off` must be `true`. See
[the delivery constitution](../docs/docs/governance/delivery.mdx) (ship bar, the guarantee) and
[ADR-0029](../docs/adr/0029-release-witness-and-value-gates-enforced.md).
