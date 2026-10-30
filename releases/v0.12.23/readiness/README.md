# v0.12.23 — readiness receipts

One receipt per leg of [`../readiness.yaml`](../readiness.yaml), named `<leg>.receipt.json`, in the
shape `release/readiness/readiness.schema.json` defines:

```json
{
  "schema_version": 1,
  "leg": "blast-radius",
  "candidate_map_sha": "<sha256 of ../candidate-images.json>",
  "input_identity": "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307",
  "result": "green",
  "findings_ref": "https://github.com/Vexa-ai/vexa/issues/<n>",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session)"
}
```

Bind the receipt to the candidate map it was taken against:

```bash
shasum -a 256 releases/v0.12.23/candidate-images.json   # → candidate_map_sha
node release/readiness/check.mjs --phase staging --release v0.12.23
```

## Status — partial backfill, deliberately not completed

Two legs are covered. Five are not, and each is absent for a stated reason — a receipt written
ahead of its finding is a false green, which is the single failure this harness exists to prevent.

| Leg | Receipt | Why |
|---|---|---|
| 3a `full-functionality` | ✅ green | `release-validate` run [32141246247](https://github.com/Vexa-ai/vexa/actions/runs/32141246247) — 9 legs SUCCESS, 3 SKIPPED (guarantee 7/8 + promote, none of them this leg's subject) |
| 4 `security-review` | ✅ green | rc.18 review final: no unauthenticated and no cross-tenant path. Findings public as [#1231](https://github.com/Vexa-ai/vexa/issues/1231), [#1232](https://github.com/Vexa-ai/vexa/issues/1232), [#1233](https://github.com/Vexa-ai/vexa/issues/1233) |
| 1 `train-value` | ⛔ none | **The leg has never run.** Its oracle exits 3 (*could not evaluate*): no `v0.12.23` tag exists on origin, so the batch `v0.12.22...v0.12.23` cannot be resolved and no truthful `input_identity` can be formed |
| 2 `blast-radius` | ⛔ none | Report final and **red** (12 ranked uncovered cells, R1 prod-blocking), but its findings have no durable public home yet — see *Publication* below |
| 3b `api-docs-sweep` | ⛔ none | Report final and **red** (5 release-blockers), same publication blocker |
| 5 `compliance-review` | ⛔ none | Report final and **red** (2 blocking gaps, 2 founder-decision-needed — and *founder decision needed* is red, not green), same publication blocker. One slice is public as [#1234](https://github.com/Vexa-ai/vexa/issues/1234) |
| 6 `promotion-ceremony` | ⛔ none | **Bound to rc.17, not rc.18.** The rehearsal packet `release-011-vexa-0.12.23` pins rc.17 digests and marks rc.18 a pending `LAST-MILE SUBSTITUTION POINT`; [vexa-platform#318](https://github.com/Vexa-ai/vexa-platform/pull/318) is open with `approved: false`, both rehearsals to be re-run, and the run-1/run-2 receipts deleted. **Re-run it — do not re-stamp it** |

### Publication

Legs 2, 3b and 5 are final but their reports exist only as internal drafts. A receipt's
`findings_ref` must point at a durable public artifact, so those three stay uncovered until their
findings are published — as issues, or as `releases/v0.12.23/readiness/<leg>.md` in this repo.
Publishing them is a disclosure decision, not a mechanical step: they carry unremediated findings.

`check.mjs` therefore still reports v0.12.23 as **not covered**. That is the correct reading, not a
harness defect: the readiness job on `release-validate` is advisory for this train
(`continue-on-error: true`) and becomes blocking on the next one.

Legs whose report examined a candidate older than the current
[`../candidate-images.json`](../candidate-images.json) must be **re-run, not re-stamped** — the
binding is the point. Leg 6 is exactly that case.
