#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  local-stitch-validate.sh --release <version> --out <dir> [--repo-root <repo>] [--apply]

Writes the local stitched-candidate validation plan. With --apply, records a
shell-level evidence packet for non-deploy checks; Compose/Lite deployment is
still performed through their dedicated skills by the agent.
USAGE
}

RELEASE=""
OUT=""
REPO_ROOT=""
APPLY="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release) RELEASE="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:-}"; shift 2 ;;
    --apply) APPLY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$RELEASE" ] || { echo "--release is required" >&2; exit 2; }
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git rev-parse --show-toplevel)"
fi

mkdir -p "$OUT"
plan="$OUT/local-stitch-validation-plan.json"
jq -n \
  --arg release "$RELEASE" \
  --arg repo_root "$REPO_ROOT" \
  --arg action "$([ "$APPLY" = "1" ] && echo apply || echo dry_run)" \
  '{
    release:$release,
    repo_root:$repo_root,
    action:$action,
    required_skills:["compose-deploy","vexa-lite-deploy","hardenloop"],
    machine_checks:[
      "source/runtime release identity",
      "browser-safe dashboard /api/config and WS URL",
      "recording route and playback API",
      "webhook delivery ledger and idempotency tests",
      "synthetic dashboard WebSocket frame proof when realtime surfaces changed"
    ],
    human_checks:[
      "only the live external-platform observations requested by vexa-meeting-deployment-test"
    ],
    forbidden:["tests3","hidden stitch-time code changes","default pack runtime ports"]
  }' > "$plan"

if [ "$APPLY" = "1" ]; then
  git -C "$REPO_ROOT" status --short > "$OUT/git-status.txt"
  git -C "$REPO_ROOT" rev-parse HEAD > "$OUT/git-head.txt"
fi

echo "wrote $plan"
