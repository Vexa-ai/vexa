#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
export NODE_PATH="$REPO/core/meetings/modules/mixed-pipeline/node_modules"
exec "$NODE_PATH/.bin/tsx" --tsconfig "$HERE/tsconfig.json" "$HERE/main.ts" "$@"
