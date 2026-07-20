# Third-party licenses — baked artifacts outside the dependency gate

`gate:licenses` (ADR-0004, `scripts/gates.mjs`) scans the resolved **npm** dependency
tree (`pnpm licenses list --json`) and the Python tree grows into `pip-licenses`. Neither
sees **non-dependency artifacts baked into the images** — most notably model weights
pulled from a model hub at build time. This file is the packaging-side complement: every
such artifact is recorded here with its license, and mirrored into the per-release SPDX
SBOM (`scripts/sbom.mjs`).

The verbatim upstream license for each entry lives under [`licenses/`](licenses/) and is
copied into the image next to the artifact it covers, so the notice travels with the bytes.

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
