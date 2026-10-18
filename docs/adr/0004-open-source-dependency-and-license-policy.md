# ADR-0004 — Open-source dependency & licence policy (FINOS-aligned)

**Status:** Accepted · 2026-06-18 · enforces **P17** · amended 2026-10-10: LGPL is Category X, CDDL and OFL are
Category B, as FINOS lists them (below)

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
- **Category B — by exception (logged):** file-scoped weak copyleft. `MPL-1.1`/`MPL-2.0`, `EPL-1.0`/
  `EPL-2.0`, `CDDL-1.0`/`CDDL-1.1`, `OFL-1.1`. Allowed **only** when used unmodified and
  dynamically/separately linked — never statically bundled into a distributed artifact — with a
  recorded exception (who/why/scope).
- **Category X — forbidden:** the GNU licences (`GPL-*`, `LGPL-*`, `AGPL-*`) and source-available /
  proprietary (`BSL`/Business Source, `SSPL`, `Elastic-2.0`, `Commons-Clause`, any non-OSI /
  "source-available").

**The categories are FINOS's own list** ([License Categories](https://community.finos.org/docs/governance/software-projects/license-categories/)),
which follows the ASF's. Until 2026-10-10 this ADR put LGPL in Category B, which FINOS does not:
FINOS lists `LGPL-2.1` and `LGPL-3.0` as Category X and `CDDL` and `OFL-1.1` as Category B. An LGPL
library therefore never enters as a dependency exception. One that only an operator-supplied runtime
needs (Qt5Core, linked by the optional native meeting wrapper; ADR-0039) is admitted by P17's
operator-supplied rule, the same rule that admits the proprietary SDK: Vexa never ships it.
`gate:licenses` classifies by this list, and holds the pull-request-time dependency review's
`allow-licenses` to it.

**Enforcement — `gate:licenses`:** scan the full resolved tree against the allowlist. The npm side uses
**pnpm's built-in licence index** (`pnpm licenses list --json`) — no extra dependency to vet, itself a P17
win. The Python side reads what each image installs from its recipe (the `uv.lock` every `uv sync`
installs, with the groups it names, and every `pip install` line) and classifies each `name==version`
against `python-licenses.json`, a reviewed index read from PyPI metadata
(`scripts/check-python-licenses.mjs --refresh`), because `uv.lock` carries no licence. A `uv sync` that
would install the `dev` group fails the gate. An `AND` expression is as restrictive as its worst term.
**Fail** on any Category X *and on any unclassified licence* (fail-safe); **require a logged exception**
(`license-exceptions.json`) for every Category B. Emit an **SBOM** (SPDX 2.3) per release so the consumer's OSPO can audit —
`scripts/sbom.mjs` inventories the npm tree (the same pnpm index), the pip tree (from the committed
`uv.lock`s), **and baked non-dependency artifacts the gate cannot see** (model weights, below); the
`release-images` workflow runs it and its `validate` leg gates on the SBOM artifact, so no release
promotes without one. Allowlist + exception log live in the repo (machine-readable), so the policy is
data, not prose.

**Transitive pruning is part of the policy.** Prefer deps with clean trees; where an optional transitive
dep drags in an encumbered licence for a feature we don't use, prune it at packaging. *Known case:* the
`@img/sharp-libvips-*` native binary (**LGPL-3.0**, Category X) behind `sharp`. The bot gets `sharp`
through `@huggingface/transformers`, which imports it at module scope for an image pipeline the
audio-only mixed lane never runs; `pnpm-workspace.yaml` overrides it with
`core/meetings/modules/no-image-backend`, a stand-in that throws a typed `ImageBackendAbsent` on any call,
so no libvips is installed for the bot or Lite. The terminal gets it as an optional dependency of `next`
that only the image optimizer loads; the optimizer is off (`clients/terminal/next.config.ts`), and the
terminal's npm project, which its images install from with `npm ci`, points `sharp` at a byte-identical
copy of the stand-in (`clients/terminal/no-image-backend`), so no stage installs libvips; both terminal
runtime trees also drop it. `gate:image-licenses` holds the overrides, the lockfiles and the prunes.
`gate:licenses` and the SBOM read that npm lockfile as well as the pnpm tree.

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
