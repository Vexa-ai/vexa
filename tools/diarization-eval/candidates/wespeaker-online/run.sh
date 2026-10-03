#!/usr/bin/env bash
# run.sh <input.wav> <output.rttm> — 16 kHz mono PCM16, CPU only.
# Dependencies (@huggingface/transformers, onnxruntime-node, tsx) come from the mixed-pipeline
# module's node_modules. Node's ESM resolver ignores NODE_PATH, so the module directory is linked
# beside main.ts (gitignored) and bare specifiers resolve by the normal walk-up.
# DIAR_THREADS is inherited by main.ts and applied to both ONNX model sessions.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
MP_NM="$REPO/core/meetings/modules/mixed-pipeline/node_modules"
[ -d "$MP_NM" ] || { echo "run.sh: $MP_NM is missing — install the mixed-pipeline module first (pnpm install --filter ./core/meetings/modules/mixed-pipeline)" >&2; exit 2; }
[ -e "$HERE/node_modules" ] || ln -s "$MP_NM" "$HERE/node_modules"
exec "$MP_NM/.bin/tsx" --tsconfig "$HERE/tsconfig.json" "$HERE/main.ts" "$@"
