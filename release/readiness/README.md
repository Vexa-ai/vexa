# `release/readiness/` — the six-leg production-readiness harness

Six legs cover a release candidate. This directory makes that coverage a tool
that fires at two named points instead of a habit that survives only while
somebody remembers it.

| # | Leg | Kind | Fires | Oracle |
|---|---|---|---|---|
| 1 | TRAIN VALUE as of PRs — every train PR carries a user-recognizable value sentence and a pass criterion | machine | formation | [`scripts/release-value-gate.mjs`](../../scripts/release-value-gate.mjs) |
| 2 | BLAST RADIUS as of PRs — per-PR diff→surface map, covering evidence, uncovered cells named | agent | formation | [`protocols/blast-radius.md`](protocols/blast-radius.md) |
| 3a | FULL FUNCTIONALITY — builds and runs in lite/compose with checks green | machine | staging | the `release-validate` legs |
| 3b | FULL FUNCTIONALITY — API coverage as of docs: every documented endpoint probed against the staged candidate | agent | staging | [`protocols/api-docs-sweep.md`](protocols/api-docs-sweep.md) |
| 4 | SECURITY — new surfaces, authz, deps in shipped images, secrets, injection | agent | both | [`protocols/security-review.md`](protocols/security-review.md) |
| 5 | COMPLIANCE — legal/privacy **+** architecture **+** principles **+** delivery process | agent | both | [`protocols/compliance-review.md`](protocols/compliance-review.md) |
| 6 | PROMOTION CEREMONY — rehearsal receipts green and bound | machine | staging | the platform rehearsal packet |

## Running it

```bash
node release/readiness/check.mjs --phase formation --release v0.12.23
node release/readiness/check.mjs --phase staging  --release v0.12.23
```

`RELEASE_VERSION` is read when `--release` is omitted, and a candidate suffix is
stripped (`v0.12.23-rc.18` → `v0.12.23`) because receipts live under the stable
release. Exit 0 is green; exit 1 is any leg missing, red, invalid, or stale.

**Formation** requires the legs that fire when the train forms. **Staging**
requires all six — a formation leg is not spent by having once been green.

## The identity binding

Every receipt records the sha256 of `releases/<version>/candidate-images.json`
at the moment its leg ran. Re-cutting a candidate rewrites that file, so every
receipt taken against the old bytes is stranded: the runner reports those legs
STALE, names each one, and prints the reason the manifest declares for it. A
security read of a diff that is no longer shipping is not coverage, and the
repair is to **re-run the leg, never to re-stamp the receipt**.

## The manifest

`releases/<version>/readiness.yaml` enumerates the legs; each declares its kind,
its oracle (a command for a machine leg, a protocol document for an agent leg),
its receipt path, its identity binding, and its firing point. The runner refuses
a manifest whose ordinals do not cover all six legs, so a leg cannot be dropped
by editing quietly. It is read by a strict YAML subset parser rather than a
dependency — release-time instruments enter no runtime image and must run on a
bare `node` — and any construct that subset does not model throws with a line
number instead of being guessed at.

## Receipts

One per leg at `releases/<version>/readiness/<leg>.receipt.json`. A receipt is a
**pointer plus a verdict**; the findings live at `findings_ref`.

```json
{
  "schema_version": 1,
  "leg": "blast-radius",
  "candidate_map_sha": "<sha256 of releases/<version>/candidate-images.json>",
  "input_identity": "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307",
  "result": "green",
  "findings_ref": "https://github.com/Vexa-ai/vexa/issues/1234",
  "generated_at": "2026-08-18T15:57:00Z",
  "generated_by": "agent (readiness session)"
}
```

`result` is `green` | `red` | `stale`. Write the receipt **when the review is
finished**, from the finished report — a receipt written ahead of its finding is
a fabricated green, which is the single failure this harness exists to prevent.
A leg with no receipt reads as uncovered, and that is the honest answer.

The shape is documented in [`readiness.schema.json`](readiness.schema.json);
[`check.mjs`](check.mjs) is the authoritative validator, and
[`check.test.mjs`](check.test.mjs) asserts the two do not drift.

## What this is not

Readiness green means the coverage exists and is bound to the candidate that is
shipping. It does **not** mean a human saw the value. The witness gate
(guarantee 7) and the value gate (guarantee 8) stay human and are untouched by
this harness — see [`releases/README.md`](../../releases/README.md).

The `readiness` job on `release-validate` is **advisory on the 0.12.23 train and
blocking on the next**: it runs with `continue-on-error` and nothing `needs` it.
Flipping it is two edits, stated in the job's own comment.
