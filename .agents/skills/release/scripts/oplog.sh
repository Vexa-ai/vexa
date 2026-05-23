#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  oplog.sh --release <version> --phase <phase> --operation <name> [options] -- <command> [args...]
  oplog.sh --release <version> --phase <phase> --operation <name> --manual --duration-ms <ms> [options]

Options:
  --kind <kind>          Operation kind, for example test, build, deploy, debug, live, human_wait.
  --evidence <path>      Evidence path or URL produced by this operation.
  --notes <text>         Short redacted note.
  --log-file <path>      Override log path. Default: .agents/releases/<release>/operation-log.jsonl.
  --manual               Append a manual timing entry instead of wrapping a command.
  --duration-ms <ms>     Required with --manual.
  --status <status>      Manual status. Default: pass.
  --exit-code <code>     Manual exit code. Default: 0.
  --started-at <iso>     Manual start time. Default: current UTC time.
  --ended-at <iso>       Manual end time. Default: current UTC time.
USAGE
}

die() {
  echo "oplog: $*" >&2
  exit 2
}

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

now_ns() {
  local value
  value="$(date +%s%N)"
  if [[ "$value" == *N ]]; then
    echo "$(( $(date +%s) * 1000000000 ))"
  else
    echo "$value"
  fi
}

redact_text() {
  sed -E \
    -e 's/vxa_[A-Za-z0-9_=-]+/vxa_***/g' \
    -e 's/(api_key=)[^&[:space:]]+/\1***/g' \
    -e 's/(Authorization:[[:space:]]*Bearer[[:space:]]+)[^[:space:]]+/\1***/g' \
    -e 's/((TRANSCRIPTION_SERVICE_TOKEN|LITE_TRANSCRIPTION_SERVICE_TOKEN|OPENAI_API_KEY|ADMIN_TOKEN|VEXA_ADMIN_API_TOKEN|ADMIN_API_TOKEN|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|MINIO_SECRET_KEY|MINIO_ACCESS_KEY|WEBHOOK_SECRET)=)[^[:space:]]+/\1***/g' \
    -e 's/(--(auth-token|admin-token|api-key|token)[=[:space:]])[^[:space:]]+/\1***/g'
}

redacted_command() {
  printf '%s ' "$@" | redact_text | sed -E 's/[[:space:]]+$//'
}

RELEASE="${RELEASE_VERSION:-}"
PHASE="${RELEASE_PHASE:-}"
OPERATION=""
KIND="command"
EVIDENCE=""
NOTES=""
LOG_FILE=""
MANUAL="0"
DURATION_MS=""
STATUS="pass"
EXIT_CODE="0"
STARTED_AT=""
ENDED_AT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)
      RELEASE="${2:-}"; shift 2 ;;
    --phase)
      PHASE="${2:-}"; shift 2 ;;
    --operation)
      OPERATION="${2:-}"; shift 2 ;;
    --kind)
      KIND="${2:-}"; shift 2 ;;
    --evidence)
      EVIDENCE="${2:-}"; shift 2 ;;
    --notes)
      NOTES="${2:-}"; shift 2 ;;
    --log-file)
      LOG_FILE="${2:-}"; shift 2 ;;
    --manual)
      MANUAL="1"; shift ;;
    --duration-ms)
      DURATION_MS="${2:-}"; shift 2 ;;
    --status)
      STATUS="${2:-}"; shift 2 ;;
    --exit-code)
      EXIT_CODE="${2:-}"; shift 2 ;;
    --started-at)
      STARTED_AT="${2:-}"; shift 2 ;;
    --ended-at)
      ENDED_AT="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    --)
      shift; break ;;
    *)
      die "unknown argument: $1" ;;
  esac
done

[[ -n "$RELEASE" ]] || die "--release or RELEASE_VERSION is required"
[[ -n "$OPERATION" ]] || die "--operation is required"
PHASE="${PHASE:-unknown}"
LOG_FILE="${LOG_FILE:-$REPO_ROOT/.agents/releases/$RELEASE/operation-log.jsonl}"
mkdir -p "$(dirname "$LOG_FILE")"

CWD="$(pwd)"
COMMAND=""

if [[ "$MANUAL" == "1" ]]; then
  [[ -n "$DURATION_MS" ]] || die "--duration-ms is required with --manual"
  STARTED_AT="${STARTED_AT:-$(utc_now)}"
  ENDED_AT="${ENDED_AT:-$(utc_now)}"
else
  [[ $# -gt 0 ]] || die "wrapped command is required after --"
  STARTED_AT="$(utc_now)"
  START_NS="$(now_ns)"
  set +e
  "$@"
  EXIT_CODE="$?"
  set -e
  END_NS="$(now_ns)"
  ENDED_AT="$(utc_now)"
  DURATION_MS="$(( (END_NS - START_NS) / 1000000 ))"
  if [[ "$EXIT_CODE" -eq 0 ]]; then
    STATUS="pass"
  else
    STATUS="fail"
  fi
  COMMAND="$(redacted_command "$@")"
fi

[[ "$DURATION_MS" =~ ^[0-9]+$ ]] || die "duration must be an integer millisecond value"
[[ "$EXIT_CODE" =~ ^[0-9]+$ ]] || die "exit code must be an integer"

jq -cn \
  --arg release "$RELEASE" \
  --arg phase "$PHASE" \
  --arg operation "$OPERATION" \
  --arg kind "$KIND" \
  --arg started_at "$STARTED_AT" \
  --arg ended_at "$ENDED_AT" \
  --arg status "$STATUS" \
  --arg cwd "$CWD" \
  --arg command "$COMMAND" \
  --arg evidence "$EVIDENCE" \
  --arg notes "$NOTES" \
  --argjson duration_ms "$DURATION_MS" \
  --argjson exit_code "$EXIT_CODE" \
  '{
    schema_version: 1,
    release: $release,
    phase: $phase,
    operation: $operation,
    kind: $kind,
    started_at: $started_at,
    ended_at: $ended_at,
    duration_ms: $duration_ms,
    status: $status,
    exit_code: $exit_code,
    cwd: $cwd,
    command: (if $command == "" then null else $command end),
    evidence: (if $evidence == "" then null else $evidence end),
    notes: (if $notes == "" then null else $notes end)
  }' >> "$LOG_FILE"

exit "$EXIT_CODE"
