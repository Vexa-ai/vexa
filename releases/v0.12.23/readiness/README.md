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

## Status — receipts pending, deliberately not fabricated

**This directory is empty of receipts on purpose.** The six-leg coverage for v0.12.23-rc.18 was
executed by hand — the train-value pass, the per-PR blast-radius map, the API sweep against staging,
the security review, the compliance review, and the promotion rehearsal all ran, and their findings
are what the harness was built from. Those reports live outside this repository today, and two of
the reviews were still open when the harness landed.

Receipts will be **backfilled from the completed reports**, one commit per leg, each carrying the
`candidate_map_sha` of the candidate its report actually examined. Until a leg's report is final,
its receipt does not exist — a receipt written ahead of its finding is a false green, which is the
single failure this harness exists to prevent.

`check.mjs` therefore reports v0.12.23 as **not covered** until the backfill lands. That is the
correct reading, not a harness defect: the readiness job on `release-validate` is advisory for this
train (`continue-on-error: true`) and becomes blocking on the next one.

Legs whose report examined a candidate older than the current
[`../candidate-images.json`](../candidate-images.json) must be **re-run, not re-stamped** — the
binding is the point.
