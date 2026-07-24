# Third-party licenses — baked artifacts outside the dependency gate

`gate:licenses` (ADR-0004, `scripts/gates.mjs`) scans the resolved **npm** dependency
tree (`pnpm licenses list --json`) and the Python tree grows into `pip-licenses`. Neither
sees **non-dependency artifacts baked into the images** — model weights pulled from a model
hub, or a service binary built from source at image-build time. This file is the
packaging-side complement: every such artifact is recorded here with its license, mirrored
into the per-release SPDX SBOM (`scripts/sbom.mjs`), and — for baked binaries and pinned
container images — audited by **`gate:image-licenses`** against
[`image-licenses.json`](image-licenses.json) (#653).

The verbatim upstream license for each entry lives under [`licenses/`](licenses/) and is
copied into the image next to the artifact it covers, so the notice travels with the bytes.

## Baked service binaries

| Artifact | Version | License | Source | Baked into | In-image path |
| --- | --- | --- | --- | --- | --- |
| `valkey` | `8.1.9` | BSD-3-Clause | [github.com/valkey-io/valkey](https://github.com/valkey-io/valkey) | `vexaai/vexa-lite` | `/usr/local/bin/valkey-server`, `/usr/local/share/valkey/LICENSE` |

**`valkey`** — the internal cache / stream store (bus, scheduler, per-dispatch streams). Lite
builds `valkey-server` + `valkey-cli` from the pinned git tag in the `valkey-builder` stage of
[`deploy/lite/Dockerfile.lite`](deploy/lite/Dockerfile.lite) (glibc-matched to the final image;
an alpine/musl binary can't run there) and copies them onto `PATH`. Valkey is the Linux
Foundation's BSD-3-Clause fork of Redis 7.2.4 — it has `XAUTOCLAIM` (parity with compose/helm)
and keeps Lite off both jammy apt's stale Redis 6.0.16 and source-available Redis ≥7.4
(RSALv2/SSPLv1). compose and helm pin `valkey/valkey:8-alpine` (operator-pulled, recorded in
[`image-licenses.json`](image-licenses.json)). Full text:
[`licenses/valkey-8.1.9.COPYING.txt`](licenses/valkey-8.1.9.COPYING.txt).

> BSD-3-Clause is a Category-A (permissive) license under ADR-0004; baking it requires no
> exception, only that the notice be preserved — which this file and the in-image
> `/usr/local/share/valkey/LICENSE` satisfy.

## Baked model weights

| Artifact | Version (revision) | License | Source | Baked into | In-image path |
| --- | --- | --- | --- | --- | --- |
| `onnx-community/pyannote-segmentation-3.0` | `main` | MIT | [huggingface.co](https://huggingface.co/onnx-community/pyannote-segmentation-3.0) | `vexaai/vexa-bot`, `vexaai/vexa-lite` | `/opt/hf-cache/LICENSE.pyannote-segmentation-3.0` |

**`onnx-community/pyannote-segmentation-3.0`** — the mixed (Zoom/Teams) speaker-diarization
lane segments speakers with this model, loaded OFFLINE from an image-baked HuggingFace cache
at `/opt/hf-cache` (see [`warm-hf-cache.mjs`](core/meetings/services/bot/warm-hf-cache.mjs)).
It is an ONNX conversion of the gated [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0);
the conversion is published MIT and **not** gated. The model card ships no LICENSE file of
its own, so the preserved notice is the [pyannote.audio](https://github.com/pyannote/pyannote-audio)
project's MIT license (`Copyright (c) 2020 CNRS`), whose copyright governs the weights. Full
text: [`licenses/onnx-community-pyannote-segmentation-3.0.LICENSE.txt`](licenses/onnx-community-pyannote-segmentation-3.0.LICENSE.txt).

> MIT is a Category-A (permissive) license under ADR-0004; baking it requires no exception,
> only that the copyright + permission notice be preserved — which this file and the baked
> `/opt/hf-cache/LICENSE.*` satisfy.
