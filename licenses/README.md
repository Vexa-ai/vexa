# `licenses/` — verbatim upstream licences for baked non-dependency artifacts

`gate:licenses` (ADR-0004) scans the **npm/pip dependency tree**. Artifacts baked into
the images that are *not* dependencies — model weights pulled from a hub at build time —
sit outside it. This directory holds the **verbatim upstream licence text** for each such
artifact, so the copyright + permission notice can travel with the bytes.

Each file here is copied into the image next to the artifact it covers (e.g.
`/opt/hf-cache/LICENSE.pyannote-segmentation-3.0`) and is indexed by the repo-root
[`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) manifest and the per-release SPDX
SBOM ([`scripts/sbom.mjs`](../scripts/sbom.mjs)).

| File | Artifact | Licence |
| --- | --- | --- |
| `onnx-community-pyannote-segmentation-3.0.LICENSE.txt` | `onnx-community/pyannote-segmentation-3.0` (mixed-lane diarization weights) | MIT |

**Adding an artifact:** drop its verbatim upstream licence here, `COPY` it next to the
artifact in the Dockerfile(s) that bake it, add a row to `THIRD_PARTY_LICENSES.md`, and (for
a fully-specified licence) register it in `scripts/sbom.mjs`.
