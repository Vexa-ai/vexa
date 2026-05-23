#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  oplog-run.sh --release <version> --skill <skill> --category <category> \
    --name <name> --out <evidence-dir> [options] -- <command> [args...]

Options:
  --parent-id <id>       Parent operation id.
  --hypothesis <text>    Hypothesis being tested.
  --result <text>        Result summary. Defaults from command exit.
  --next <text>          Next action.
  --evidence <path>      Extra evidence path. May be repeated.
  --op-id <id>           Override generated operation id.
  --log-file <path>      Override JSONL path. Default: .agents/releases/<release>/ops/ops.jsonl.
  --redaction-reason <text>
                         Replace the recorded command with a redaction note.
  --manual               Write a span for a non-shell operation.
  --started-at <iso>     Manual start time.
  --ended-at <iso>       Manual end time.
  --duration-ms <ms>     Manual duration in milliseconds.
  --status <status>      Manual status, default pass.
  --exit-code <code>     Manual exit code, default 0.
USAGE
}

die() {
  echo "oplog-run: $*" >&2
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

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g' \
    | cut -c1-80
}

relpathish() {
  local path="$1"
  case "$path" in
    "$REPO_ROOT"/*) printf '%s' "${path#"$REPO_ROOT/"}" ;;
    ./*) printf '%s' "${path#./}" ;;
    *) printf '%s' "$path" ;;
  esac
}

redact_text() {
  sed -E \
    -e 's#https://meet\.google\.com/[A-Za-z0-9?&=_%-]+#https://meet.google.com/***#g' \
    -e 's#https://teams\.microsoft\.com/meet/[A-Za-z0-9?&=_%-]+#https://teams.microsoft.com/meet/***#g' \
    -e 's/vxa_[A-Za-z0-9_=-]+/vxa_***/g' \
    -e 's/(api_key=)[^&[:space:]]+/\1***/g' \
    -e 's/(Authorization:[[:space:]]*Bearer[[:space:]]+)[^[:space:]]+/\1***/g' \
    -e 's/((TRANSCRIPTION_SERVICE_TOKEN|LITE_TRANSCRIPTION_SERVICE_TOKEN|OPENAI_API_KEY|ADMIN_TOKEN|VEXA_ADMIN_API_TOKEN|ADMIN_API_TOKEN|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|MINIO_SECRET_KEY|MINIO_ACCESS_KEY|WEBHOOK_SECRET)=)[^[:space:]]+/\1***/g' \
    -e 's/(--(auth-token|admin-token|api-key|token)[=[:space:]])[^[:space:]]+/\1***/g'
}

redacted_command() {
  printf '%q ' "$@" | redact_text | sed -E 's/[[:space:]]+$//'
}

allowed_category() {
  case "$1" in
    inspect|edit|test|build|deploy|browser-proof|live-meeting|wait-human|wait-service|debug|cleanup|decision) return 0 ;;
    *) return 1 ;;
  esac
}

RELEASE="${RELEASE_VERSION:-}"
SKILL=""
CATEGORY=""
NAME=""
OUT_DIR=""
PARENT_ID=""
HYPOTHESIS=""
RESULT=""
NEXT_ACTION=""
OP_ID=""
LOG_FILE=""
REDACTION_REASON=""
EXTRA_EVIDENCE=()
MANUAL="0"
MANUAL_STARTED_AT=""
MANUAL_ENDED_AT=""
MANUAL_DURATION_MS=""
MANUAL_STATUS="pass"
MANUAL_EXIT_CODE="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) RELEASE="${2:-}"; shift 2 ;;
    --skill) SKILL="${2:-}"; shift 2 ;;
    --category) CATEGORY="${2:-}"; shift 2 ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --parent-id) PARENT_ID="${2:-}"; shift 2 ;;
    --hypothesis) HYPOTHESIS="${2:-}"; shift 2 ;;
    --result) RESULT="${2:-}"; shift 2 ;;
    --next) NEXT_ACTION="${2:-}"; shift 2 ;;
    --evidence) EXTRA_EVIDENCE+=("${2:-}"); shift 2 ;;
    --op-id) OP_ID="${2:-}"; shift 2 ;;
    --log-file) LOG_FILE="${2:-}"; shift 2 ;;
    --redaction-reason) REDACTION_REASON="${2:-}"; shift 2 ;;
    --manual) MANUAL="1"; shift ;;
    --started-at) MANUAL_STARTED_AT="${2:-}"; shift 2 ;;
    --ended-at) MANUAL_ENDED_AT="${2:-}"; shift 2 ;;
    --duration-ms) MANUAL_DURATION_MS="${2:-}"; shift 2 ;;
    --status) MANUAL_STATUS="${2:-}"; shift 2 ;;
    --exit-code) MANUAL_EXIT_CODE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$RELEASE" ]] || die "--release or RELEASE_VERSION is required"
[[ -n "$SKILL" ]] || die "--skill is required"
[[ -n "$CATEGORY" ]] || die "--category is required"
allowed_category "$CATEGORY" || die "unsupported category: $CATEGORY"
[[ -n "$NAME" ]] || die "--name is required"
if [[ "$MANUAL" != "1" ]]; then
  [[ $# -gt 0 ]] || die "wrapped command is required after --"
else
  [[ -n "$MANUAL_DURATION_MS" ]] || die "--duration-ms is required with --manual"
  [[ "$MANUAL_DURATION_MS" =~ ^[0-9]+$ ]] || die "--duration-ms must be an integer"
  [[ "$MANUAL_EXIT_CODE" =~ ^[0-9]+$ ]] || die "--exit-code must be an integer"
fi

STARTED_AT="${MANUAL_STARTED_AT:-$(utc_now)}"
START_NS="$(now_ns)"
OP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OP_ID="${OP_ID:-$OP_STAMP-$(slugify "$CATEGORY-$NAME")}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/.agents/releases/$RELEASE/ops/$OP_ID}"
LOG_FILE="${LOG_FILE:-$REPO_ROOT/.agents/releases/$RELEASE/ops/ops.jsonl}"
mkdir -p "$OUT_DIR" "$(dirname "$LOG_FILE")"

STDOUT_LOG="$OUT_DIR/stdout.log"
STDERR_LOG="$OUT_DIR/stderr.log"

if [[ "$MANUAL" == "1" ]]; then
  COMMAND="[manual operation]"
elif [[ -n "$REDACTION_REASON" ]]; then
  COMMAND="[redacted: $REDACTION_REASON]"
else
  COMMAND="$(redacted_command "$@")"
fi

BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null || true)" ]]; then
  DIRTY="true"
else
  DIRTY="false"
fi

if [[ "$MANUAL" == "1" ]]; then
  : > "$STDOUT_LOG"
  : > "$STDERR_LOG"
  EXIT_CODE="$MANUAL_EXIT_CODE"
  STATUS="$MANUAL_STATUS"
  ENDED_AT="${MANUAL_ENDED_AT:-$(utc_now)}"
  DURATION_MS="$MANUAL_DURATION_MS"
  RESULT="${RESULT:-manual operation recorded}"
else
  set +e
  "$@" > >(tee "$STDOUT_LOG") 2> >(tee "$STDERR_LOG" >&2)
  EXIT_CODE="$?"
  set -e

  END_NS="$(now_ns)"
  ENDED_AT="$(utc_now)"
  DURATION_MS="$(( (END_NS - START_NS) / 1000000 ))"
  if [[ "$EXIT_CODE" -eq 0 ]]; then
    STATUS="pass"
    RESULT="${RESULT:-completed successfully}"
  else
    STATUS="fail"
    RESULT="${RESULT:-failed with exit code $EXIT_CODE}"
  fi
fi

EVIDENCE_LINES="$(
  {
    relpathish "$STDOUT_LOG"; printf '\n'
    relpathish "$STDERR_LOG"; printf '\n'
    for evidence in "${EXTRA_EVIDENCE[@]}"; do
      relpathish "$evidence"; printf '\n'
    done
  } | awk 'NF'
)"
EVIDENCE_JSON="$(printf '%s\n' "$EVIDENCE_LINES" | jq -R -s 'split("\n") | map(select(length > 0))')"

jq -cn \
  --arg op_id "$OP_ID" \
  --arg parent_id "$PARENT_ID" \
  --arg release "$RELEASE" \
  --arg skill "$SKILL" \
  --arg category "$CATEGORY" \
  --arg name "$NAME" \
  --arg started_at "$STARTED_AT" \
  --arg ended_at "$ENDED_AT" \
  --arg status "$STATUS" \
  --arg command "$COMMAND" \
  --arg cwd "$(relpathish "$(pwd)")" \
  --arg branch "$BRANCH" \
  --arg commit "$COMMIT" \
  --arg dirty "$DIRTY" \
  --arg hypothesis "$HYPOTHESIS" \
  --arg result "$RESULT" \
  --arg next "$NEXT_ACTION" \
  --arg redaction_reason "$REDACTION_REASON" \
  --argjson duration_ms "$DURATION_MS" \
  --argjson exit_code "$EXIT_CODE" \
  --argjson evidence "$EVIDENCE_JSON" \
  '{
    op_id: $op_id,
    parent_id: (if $parent_id == "" then null else $parent_id end),
    release: $release,
    skill: $skill,
    category: $category,
    name: $name,
    started_at: $started_at,
    ended_at: $ended_at,
    duration_ms: $duration_ms,
    status: $status,
    exit_code: $exit_code,
    command: $command,
    cwd: $cwd,
    git: {
      branch: (if $branch == "" then null else $branch end),
      commit: (if $commit == "" then null else $commit end),
      dirty: ($dirty == "true")
    },
    evidence: $evidence,
    hypothesis: (if $hypothesis == "" then null else $hypothesis end),
    result: $result,
    next: (if $next == "" then null else $next end),
    redaction_reason: (if $redaction_reason == "" then null else $redaction_reason end)
  }' >> "$LOG_FILE"

exit "$EXIT_CODE"
