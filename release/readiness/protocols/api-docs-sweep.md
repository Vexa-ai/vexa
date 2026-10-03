# API-docs sweep protocol

Proves that every route, parameter, status code and response field the **published docs** promise
behaves as documented against the staged candidate — the API-coverage half of the full-functionality
leg, measured as-of-docs rather than as-of-code.

**Leg id:** `api-docs-sweep` · **fires:** `staging` · **re-run:** on every candidate re-cut and on any
change under `docs/docs/api/`.

## Inputs

| Input | How to obtain |
|---|---|
| Documented surface at the candidate | `git show <head>:docs/docs/api/meetings.mdx` (and `calendar.mdx`, `agent.mdx`, `errors.mdx`) |
| Docs tree sha | `git rev-parse <head>:docs/docs/api` |
| Staged base URL | the environment under the stage lock, e.g. `https://api.staging.vexa.ai` |
| Proof the stage runs the candidate | `kubectl get pods -o jsonpath='{..imageID}'` ∩ `releases/<version>/candidate-images.json` digests; record the Helm revision |
| API key(s) | a throwaway account per the scopes the docs name (`bot`, `tx`, `browser`); never a customer key, never prod |
| Candidate map | `releases/<version>/candidate-images.json` |

Read the docs from the **candidate commit**, not from the docs site — the site serves the last
published build and will not carry this train's edits.

## Input identity

The documented surface swept, and the deployment it was probed against — the shape
`releases/<version>/readiness.yaml` pins for this leg:

```
docs@8b31abbb8265588daf89e65456e98d4988bed327 against https://api.staging.vexa.ai
```

```bash
printf 'docs@%s against %s\n' "$(git rev-parse "$HEAD":docs/docs/api)" "$API_BASE"
```

Either half moving — an edit under `docs/docs/api/`, or a redeploy — voids the receipt. Record the Helm
revision and the deployed image digests in the findings.

## Method

1. **Enumerate the legs from the docs, and write the list down before probing.** One leg per documented
   promise, not per endpoint. The enumeration is the denominator and it is fixed before any request is
   sent. Sources of legs:
   - every `bash <METHOD> <path>` block in each `.mdx`
   - every row of each page's **Error summary** table (`409` on the cap, `422` on invalid input, `404`
     on unknown/deleted/disabled, `503` when unwired, `204` with no body)
   - every documented **compatibility / legacy** endpoint — documented means in the denominator, however
     loudly the page says new integrations should use the plural API
   - every documented **projection or redaction** claim (e.g. "every response edge projects
     `calendar_sources` to exactly these four keys"; "the feed URL is never returned in full")
   - every documented **default** (`auto_join` default, `bot_name` fallback chain, caps such as ten
     active connections, 100-character names)

   The rc.18 calendar sweep ran **14** legs on this basis: create · list · cap · patch · sync · dedup ·
   edit · cancel · opt-out · legacy · encoding · delete · errors (13 passing, one failing).

2. **Verify the environment is the candidate before the first probe.** Deployed imageIDs must appear in
   `candidate-images.json`. If they do not, stop — the sweep proves nothing about this release.

3. **Write each assertion from the doc, then send the request.** The documented status code, the
   documented field names, the documented cardinality. Never read the response first and write the
   assertion around it; that measures the implementation and calls it the docs.

4. **Probe in lifecycle sequence, not as isolated calls.** Legs share state and the ordering is part of
   the surface: create → list → hit the cap → patch → sync → re-sync for dedup → edit the imported plan
   → cancel → per-meeting opt-out → the legacy singular route → hostile encoding → delete → error table.
   A leg that only passes from a clean account is not the documented behaviour.

5. **Include the hostile-input legs.** Path parameters carrying control characters, percent-encodings,
   dot segments and over-long ids: the documented answer is a `4xx`, and any `5xx` is a leg failure
   regardless of what the body says.

6. **Record per leg: name · request · observed status · body excerpt · PASS/FAIL.** For every FAIL,
   decide and state which artefact is wrong — the code or the doc — because the remedies go to different
   places. Never leave it as "discrepancy".

7. **Re-prove a fixed FAIL on the revision that carries the fix.** The denominator never shrinks: a leg
   removed because it failed is a false green. rc.18's single FAIL (an auto-join retry storm) was fixed
   and re-proven live before the leg counted.

8. **Cross-check the documented surface against the sealed contract.** Any route documented and served
   but absent from `core/gateway/contracts/api.v1/` is a real finding — hand it to the compliance leg
   (§architecture) rather than absorbing it here. This leg's verdict is about docs↔runtime; contract
   sealing is compliance's.

9. **Commit the sweep as a script under `release/readiness/`.** An uncommitted sweep is a measurement,
   not a gate — the next change to these routes has no barrier. This is the single most common defect in
   this leg's history.

10. **Tear down.** Delete probe calendars, stop probe bots, disarm anything that auto-joins, and record
    the teardown in the findings. A probe feed left armed on a staging replica is how a bot walks into a
    stranger's meeting.

## Receipt

`releases/v0.12.23/readiness/api-docs-sweep.receipt.json`:

```json
{
  "schema_version": 1,
  "leg": "api-docs-sweep",
  "candidate_map_sha": "3422d02f02985ab0f02fc47f58a6b4b3e23f0163397f1073c004fe44624d764f",
  "input_identity": "docs@8b31abbb8265588daf89e65456e98d4988bed327 against https://api.staging.vexa.ai",
  "result": "green",
  "findings_ref": "releases/v0.12.23/readiness/api-docs-sweep.md",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session · api-docs-sweep)"
}
```

```bash
V=v0.12.23; mkdir -p releases/$V/readiness
MAP_SHA=$(shasum -a 256 releases/$V/candidate-images.json | cut -d' ' -f1)
jq -n --arg s "$MAP_SHA" --arg i "$IDENTITY" --arg r green --arg f "$FINDINGS_REF" \
  '{schema_version:1,leg:"api-docs-sweep",candidate_map_sha:$s,input_identity:$i,result:$r,
    findings_ref:$f,generated_at:(now|todate),generated_by:"agent (readiness session)"}' \
  > releases/$V/readiness/api-docs-sweep.receipt.json
node release/readiness/check.mjs --phase staging --release $V
```

The findings file carries the full leg table with the `N/M` count. The receipt carries only the verdict
and the pointer. A re-cut changes the map sha and this receipt goes `stale`.

## Blocking-verdict discipline

`green` requires **every** enumerated leg PASS on the deployed candidate. `N-1/N` is `red`, not "green
with a note" — the missing leg is a published promise the product does not keep.

**RED — blocks the train:**
- A documented route answers `404`, `5xx`, or a status the docs' own error table does not list.
- A documented field is absent, renamed, or carries a different type.
- A documented redaction or projection claim fails — a credential or an internal snapshot rides a
  response the docs say it never rides. This one blocks even at one occurrence.
- A leg passes only because of a fix that is **unmerged**. An unmerged fix is not shipped.
- The stage is not running candidate digests.

**Rides to the next train:** wording and example drift that does not change behaviour; documented-but-
unimplemented *future* surfaces already marked as such; undocumented-but-shipped routes (that is
compliance's contract finding, filed here and carried there).

**Waivers:** founder only, per leg, recorded in the receipt's `waivers[]` with the leg name, the issue
link and the release it expires in. A docs fix is usually cheaper than a waiver — prefer correcting the
page and re-running the leg.

## Failure modes

- **Sweeping an environment that is not the candidate.** A green sweep against yesterday's revision is a
  green sweep of yesterday's release.
- **Writing the assertion after seeing the response.** This converts every code defect into a docs
  update and the leg can never fail.
- **Counting a leg green off an unmerged fix**, or off a fix present in a branch build but not in the
  published candidate images.
- **Running the sweep by hand.** rc.18's 13/14 had no committed script — a real, well-designed
  measurement that nobody, including us, can reproduce from the repo.
- **Dropping the legacy/compat endpoints** because the page recommends the newer API. They are
  documented, so existing integrations depend on them, so they are legs.
- **Testing the happy path only.** The error table is roughly half the documented surface; a `409` cap
  and a `503`-when-unwired are promises exactly as much as a `201`.
- **Leaving probe state behind** — armed feeds, live bots, or a throwaway account with auto-join on.
