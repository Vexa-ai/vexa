# ADR-0004 — Open-source dependency & licence policy (FINOS-aligned)

**Status:** Accepted · 2026-06-18 · enforces **P17**

## Context

Vexa must be deployable *inside* regulated organisations (banks, insurers). Their legal/OSPO review
rejects any artifact whose dependency tree — **direct or transitive** — carries a copyleft or
source-available licence. A single GPL/AGPL or BSL/SSPL package buried five levels deep can block the
whole platform. This is a hard deployment constraint, so it must be a **gated** rule, not a guideline.
[FINOS](https://www.finos.org/) (the Fintech Open Source Foundation, under the Linux Foundation) exists
largely to govern this; we adopt its posture and the ASF licence-category model.

## Decision

**Three licence categories** decide every dependency (and its transitive closure):

- **Category A — allowed (auto):** OSI-approved permissive. `Apache-2.0`, `MIT`, `BSD-2-Clause`,
  `BSD-3-Clause`, `ISC`, `0BSD`, `Unlicense`, `CC0-1.0`, `Python-2.0`, `BlueOak-1.0.0`, `Zlib`.
- **Category B — by exception (logged):** weak / file-scoped copyleft. `MPL-2.0`, `EPL-2.0`,
  `LGPL-2.1`/`LGPL-3.0`. Allowed **only** when used unmodified and dynamically/separately linked —
  never statically bundled into a distributed artifact — with a recorded exception (who/why/scope).
- **Category X — forbidden:** strong copyleft (`GPL-*`, `AGPL-*`) and source-available / proprietary
  (`BSL`/Business Source, `SSPL`, `Elastic-2.0`, `Commons-Clause`, any non-OSI / "source-available").

**Enforcement — `gate:licenses`:** scan the full resolved tree against the allowlist. The npm side uses
**pnpm's built-in licence index** (`pnpm licenses list --json`) — no extra dependency to vet, itself a P17
win; the Python side adds `pip-licenses` when those deps grow. **Fail** on any Category X *and on any
unclassified licence* (fail-safe); **require a logged exception** (`license-exceptions.json`) for every
Category B. Emit an **SBOM** (SPDX 2.3) per release so the consumer's OSPO can audit —
`scripts/sbom.mjs` inventories the npm tree (the same pnpm index), the pip tree (from the committed
`uv.lock`s), **and baked non-dependency artifacts the gate cannot see** (model weights, below); the
`release-images` workflow runs it and its `validate` leg gates on the SBOM artifact, so no release
promotes without one. Allowlist + exception log live in the repo (machine-readable), so the policy is
data, not prose.

**Transitive pruning is part of the policy.** Prefer deps with clean trees; where an optional transitive
dep drags in an encumbered licence for a feature we don't use, prune it at packaging. *Known case:* the
`@img/sharp-libvips-*` native binary (**LGPL-3.0**) enters via `sharp` ← `@huggingface/transformers`'s
**image** pipeline — Vexa's mixed lane is **audio-only** and never loads it. It is logged as a Category-B
exception (`license-exceptions.json`: LGPL, dynamically linked, unmodified — compliant) **and** pruned
from the deployment artifact (`--no-optional`), so no LGPL binary ships. Audit (2026-06-18): **112 of 113
npm deps are Category A**; this is the only non-permissive one.

**Baked artifacts are covered outside the dependency gate.** `gate:licenses` scans the resolved
*dependency* tree; it cannot see bytes baked into an image that are not npm/pip deps — notably model
weights pulled from a hub at build time. The mixed (Zoom/Teams) lane bakes
`onnx-community/pyannote-segmentation-3.0` (**MIT**, Category A) into `vexaai/vexa-bot` and
`vexaai/vexa-lite` at `/opt/hf-cache`. It is recorded three ways: the notice travels with the weights
(`/opt/hf-cache/LICENSE.pyannote-segmentation-3.0`), the repo manifest [`THIRD_PARTY_LICENSES.md`] lists
it, and the per-release SBOM (`scripts/sbom.mjs`) emits it as a fully-specified package. Any new baked
artifact follows the same three-step record — the packaging-side complement to this gate.

## Consequences

- A one-time audit of the current tree precedes turning the gate red; thereafter every new dep is gated.
- Most of our stack is already Category A (TS: ajv/tsx/esbuild/ws/zod = MIT, typescript/playwright/
  transformers = Apache-2.0; Py: pydantic/jsonschema/fastapi/pytest = MIT, httpx = BSD). The work is the
  *tail* and the *transitive* closure — exactly what manual review misses and the gate catches.
- The vendored dashboard (47-dep Next.js, pending refactor) is **out of the gate** for now (`.gateignore`);
  its tree gets the full audit as part of that refactor.
- Native postinstall builds (`onnxruntime-node`, `protobufjs`) are a separate supply-chain concern
  (pnpm `allowBuilds`), decided per-package; licence-clean ≠ build-script-trusted.
