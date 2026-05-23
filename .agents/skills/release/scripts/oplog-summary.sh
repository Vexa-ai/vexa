#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  oplog-summary.sh --release <version> [--limit <n>]
  oplog-summary.sh --log-file <path> [--limit <n>]
USAGE
}

die() {
  echo "oplog-summary: $*" >&2
  exit 2
}

RELEASE="${RELEASE_VERSION:-}"
LOG_FILE=""
LIMIT="10"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)
      RELEASE="${2:-}"; shift 2 ;;
    --log-file)
      LOG_FILE="${2:-}"; shift 2 ;;
    --limit)
      LIMIT="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      if [[ -z "$LOG_FILE" && -f "$1" ]]; then
        LOG_FILE="$1"; shift
      else
        die "unknown argument: $1"
      fi ;;
  esac
done

if [[ -z "$LOG_FILE" ]]; then
  [[ -n "$RELEASE" ]] || die "--release or --log-file is required"
  LOG_FILE="$REPO_ROOT/.agents/releases/$RELEASE/ops/ops.jsonl"
fi

[[ "$LIMIT" =~ ^[0-9]+$ ]] || die "--limit must be an integer"
[[ -s "$LOG_FILE" ]] || die "log file is missing or empty: $LOG_FILE"

echo "Slowest operations"
printf 'duration_s\tstatus\tcategory\tskill\tname\tevidence\n'
jq -rs --argjson limit "$LIMIT" '
  sort_by(.duration_ms)
  | reverse
  | .[:$limit]
  | .[]
  | [((.duration_ms / 1000) | tostring), .status, .category, (.skill // ""), .name, ((.evidence // []) | join(","))]
  | @tsv
' "$LOG_FILE"

echo
echo "Totals by category and operation"
printf 'total_s\tcount\tmax_s\tavg_s\tcategory\tname\n'
jq -rs '
  sort_by(.category, .name)
  | group_by([.category, .name])
  | map({
      category: .[0].category,
      name: .[0].name,
      count: length,
      total_ms: (map(.duration_ms) | add),
      max_ms: (map(.duration_ms) | max)
    })
  | map(. + { avg_ms: (.total_ms / .count) })
  | sort_by(.total_ms)
  | reverse
  | .[]
  | [((.total_ms / 1000) | tostring), (.count | tostring), ((.max_ms / 1000) | tostring), ((.avg_ms / 1000) | tostring), .category, .name]
  | @tsv
' "$LOG_FILE"
