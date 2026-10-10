# v0.12.28 — the installation repair release

The candidate map binds the eleven images published as `v0.12.28-rc.5`. They come from four
build sources, each tree-identical (on that image's runtime inputs) to `main` at `d90708c10`:

| Images | Bytes | Build source |
|---|---|---|
| admin-api, runtime, gateway, mcp, agent-api, agent-worker, flows | carried unchanged from v0.12.27 | `71321ad0` (v0.12.27-rc.5, run 34051273765) |
| meeting-api, terminal | rebuilt | `c1d0ef6f0` (v0.12.28-rc.2, run 37847760812) |
| lite | rebuilt | `d90708c10` (v0.12.28-rc.4, run 37909519615); #1793 changed `scripts/`, a Lite input |
| bot | the image vexa.ai production runs | `2b60c6ce9` = Vexa-ai/vexa#1781 head, tree equal to `c1d0ef6f0` (v0.12.28-zoom.2) |

`v0.12.28-rc.5` was assembled by tagging those exact manifests (no rebuild) and reading each one
back. It supersedes `v0.12.28-rc.3`, which carried the `c1d0ef6f0` Lite. The superseded `v0.12.28-rc.1` packet (Vexa-ai/vexa#1768) predates #1778 and #1781.

Validation and witness evidence are recorded in `candidate-images.json` and `witness.json`.
