#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  pack-isolation-check.sh --release <version> --pack <P0|P1|P2|P3|P4> --out <file> [options]

Records a JSON preflight for an experimental vertical pack-isolation lane.
The check is synthetic-first: it reports whether a real meeting is needed now,
but it does not request or use live Google Meet / Teams rooms.

Options:
  --worktree PATH
  --branch NAME
  --base-commit SHA
  --runtime-prefix NAME
  --lite-dashboard-port PORT
  --lite-gateway-port PORT
  --compose-dashboard-port PORT
  --compose-gateway-port PORT
  --evidence-root PATH
  -h, --help
USAGE
}

die() {
  echo "pack-isolation-check: $*" >&2
  exit 2
}

json_array_from_lines() {
  jq -R -s 'split("\n") | map(select(length > 0))'
}

port_listeners() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$port" 2>/dev/null | tail -n +2 || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  else
    true
  fi
}

RELEASE=""
PACK=""
OUT=""
WORKTREE=""
BRANCH=""
BASE_COMMIT=""
RUNTIME_PREFIX=""
LITE_DASHBOARD_PORT=""
LITE_GATEWAY_PORT=""
COMPOSE_DASHBOARD_PORT=""
COMPOSE_GATEWAY_PORT=""
EVIDENCE_ROOT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release) RELEASE="${2:-}"; shift 2 ;;
    --pack) PACK="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --base-commit) BASE_COMMIT="${2:-}"; shift 2 ;;
    --runtime-prefix) RUNTIME_PREFIX="${2:-}"; shift 2 ;;
    --lite-dashboard-port) LITE_DASHBOARD_PORT="${2:-}"; shift 2 ;;
    --lite-gateway-port) LITE_GATEWAY_PORT="${2:-}"; shift 2 ;;
    --compose-dashboard-port) COMPOSE_DASHBOARD_PORT="${2:-}"; shift 2 ;;
    --compose-gateway-port) COMPOSE_GATEWAY_PORT="${2:-}"; shift 2 ;;
    --evidence-root) EVIDENCE_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$RELEASE" ] || die "--release is required"
[ -n "$PACK" ] || die "--pack is required"
[ -n "$OUT" ] || die "--out is required"
command -v jq >/dev/null 2>&1 || die "jq is required"

PACK="$(printf '%s' "$PACK" | tr '[:lower:]' '[:upper:]')"

if [ "$RELEASE" = "0.10.6.3" ]; then
  BASE_COMMIT="${BASE_COMMIT:-6da3abb92fd50c1939f1c0ecfb85246db364e427}"
  case "$PACK" in
    P0|PACK0)
      PACK="P0"
      PACK_NAME="PACK 0-baseline-proof"
      WORKTREE="${WORKTREE:-/home/dima/dev/vexa-260523-v0.10.6.3-from-0.10.6}"
      BRANCH="${BRANCH:-codex/release-0.10.6.3-from-0.10.6}"
      RUNTIME_PREFIX="${RUNTIME_PREFIX:-vexa-1063-baseline}"
      LITE_DASHBOARD_PORT="${LITE_DASHBOARD_PORT:-33000}"
      LITE_GATEWAY_PORT="${LITE_GATEWAY_PORT:-38056}"
      COMPOSE_DASHBOARD_PORT="${COMPOSE_DASHBOARD_PORT:-}"
      COMPOSE_GATEWAY_PORT="${COMPOSE_GATEWAY_PORT:-}"
      EVIDENCE_ROOT="${EVIDENCE_ROOT:-.agents/releases/0.10.6.3/baseline}"
      REAL_MEETING_NEEDED_NOW="false"
      REAL_MEETING_WHEN="not needed; Pack 0 uses synthetic baseline proof only"
      SYNTHETIC_NEXT='["verify stable Lite baseline lane","verify no exposed internal Meeting API","prove browser WS frame delivery from synthetic active meeting","reject REST/DOM-only evidence"]'
      ;;
    P1|PACK1)
      PACK="P1"
      PACK_NAME="PACK 1-recording-trust"
      WORKTREE="${WORKTREE:-/home/dima/dev/vexa-260523-v0.10.6.3-p1-recording-trust}"
      BRANCH="${BRANCH:-codex/release-0.10.6.3-p1-recording-trust}"
      RUNTIME_PREFIX="${RUNTIME_PREFIX:-vexa-1063-rec}"
      LITE_DASHBOARD_PORT="${LITE_DASHBOARD_PORT:-33100}"
      LITE_GATEWAY_PORT="${LITE_GATEWAY_PORT:-38156}"
      COMPOSE_DASHBOARD_PORT="${COMPOSE_DASHBOARD_PORT:-33101}"
      COMPOSE_GATEWAY_PORT="${COMPOSE_GATEWAY_PORT:-38157}"
      EVIDENCE_ROOT="${EVIDENCE_ROOT:-.agents/releases/0.10.6.3/packs/P1-recording-trust}"
      REAL_MEETING_NEEDED_NOW="false"
      REAL_MEETING_WHEN="not needed for isolated Pack 1 gate"
      SYNTHETIC_NEXT='["replay recording-trust hunks only","run recording/finalizer/storage/sweep tests","prove canonical gateway recording route","prove dashboard master playback from synthetic completed meeting","prove Lite recording persistence if Lite files are touched"]'
      ;;
    P2|PACK2)
      PACK="P2"
      PACK_NAME="PACK 2-speak-and-meeting-actions"
      WORKTREE="${WORKTREE:-/home/dima/dev/vexa-260523-v0.10.6.3-p2-speak-actions}"
      BRANCH="${BRANCH:-codex/release-0.10.6.3-p2-speak-actions}"
      RUNTIME_PREFIX="${RUNTIME_PREFIX:-vexa-1063-speak}"
      LITE_DASHBOARD_PORT="${LITE_DASHBOARD_PORT:-33200}"
      LITE_GATEWAY_PORT="${LITE_GATEWAY_PORT:-38256}"
      COMPOSE_DASHBOARD_PORT="${COMPOSE_DASHBOARD_PORT:-33201}"
      COMPOSE_GATEWAY_PORT="${COMPOSE_GATEWAY_PORT:-38257}"
      EVIDENCE_ROOT="${EVIDENCE_ROOT:-.agents/releases/0.10.6.3/packs/P2-speak-and-meeting-actions}"
      REAL_MEETING_NEEDED_NOW="false"
      REAL_MEETING_WHEN="after TTS/callback/bot-config/Teams/GMeet/mocked speak checks pass, for platform admission and human audible conversation only"
      SYNTHETIC_NEXT='["run TTS validation and multilingual probes","run bot camera/config checks","run Teams modal and GMeet admission tests","run mocked or dry-run speak proof","then request real meeting only for external platform and human sensory facts"]'
      ;;
    P3|PACK3)
      PACK="P3"
      PACK_NAME="PACK 3-lifecycle-and-billing-webhook-trust"
      WORKTREE="${WORKTREE:-/home/dima/dev/vexa-260523-v0.10.6.3-p3-lifecycle-billing}"
      BRANCH="${BRANCH:-codex/release-0.10.6.3-p3-lifecycle-billing}"
      RUNTIME_PREFIX="${RUNTIME_PREFIX:-vexa-1063-life}"
      LITE_DASHBOARD_PORT="${LITE_DASHBOARD_PORT:-33300}"
      LITE_GATEWAY_PORT="${LITE_GATEWAY_PORT:-38356}"
      COMPOSE_DASHBOARD_PORT="${COMPOSE_DASHBOARD_PORT:-33301}"
      COMPOSE_GATEWAY_PORT="${COMPOSE_GATEWAY_PORT:-38357}"
      EVIDENCE_ROOT="${EVIDENCE_ROOT:-.agents/releases/0.10.6.3/packs/P3-lifecycle-and-billing-webhook-trust}"
      REAL_MEETING_NEEDED_NOW="false"
      REAL_MEETING_WHEN="not needed for isolated Pack 3 gate"
      SYNTHETIC_NEXT='["run synthetic stop/delete terminal lifecycle proof","run duplicate run_all_tasks billing-hook idempotency proof","run outbound ledger stale pending sweep proof","run public webhook payload/header compatibility tests","run runtime callback lifecycle tests"]'
      ;;
    P4|PACK4)
      PACK="P4"
      PACK_NAME="PACK 4-self-hosted-browser-lite-realtime"
      WORKTREE="${WORKTREE:-/home/dima/dev/vexa-260523-v0.10.6.3-p4-selfhost-realtime}"
      BRANCH="${BRANCH:-codex/release-0.10.6.3-p4-selfhost-realtime}"
      RUNTIME_PREFIX="${RUNTIME_PREFIX:-vexa-1063-edge}"
      LITE_DASHBOARD_PORT="${LITE_DASHBOARD_PORT:-33400}"
      LITE_GATEWAY_PORT="${LITE_GATEWAY_PORT:-38456}"
      COMPOSE_DASHBOARD_PORT="${COMPOSE_DASHBOARD_PORT:-33401}"
      COMPOSE_GATEWAY_PORT="${COMPOSE_GATEWAY_PORT:-38457}"
      EVIDENCE_ROOT="${EVIDENCE_ROOT:-.agents/releases/0.10.6.3/packs/P4-self-hosted-browser-lite-realtime}"
      REAL_MEETING_NEEDED_NOW="false"
      REAL_MEETING_WHEN="not needed for isolated Pack 4 gate unless synthetic proof exposes an external-platform-only dependency"
      SYNTHETIC_NEXT='["run dashboard config/auth cookie tests","prove Lite/Compose exposed ports and internal ports","create two synthetic active meetings with same native id","prove exact meeting_id subscribe/ack/frame","prove legacy native-id compatibility","reject REST/DOM-only evidence"]'
      ;;
    *) die "unsupported pack for $RELEASE: $PACK" ;;
  esac
else
  [ -n "$WORKTREE" ] || die "--worktree is required for releases without built-in pack defaults"
  [ -n "$BRANCH" ] || die "--branch is required for releases without built-in pack defaults"
  [ -n "$BASE_COMMIT" ] || die "--base-commit is required for releases without built-in pack defaults"
  [ -n "$RUNTIME_PREFIX" ] || die "--runtime-prefix is required for releases without built-in pack defaults"
  [ -n "$EVIDENCE_ROOT" ] || die "--evidence-root is required for releases without built-in pack defaults"
  PACK_NAME="$PACK"
  REAL_MEETING_NEEDED_NOW="false"
  REAL_MEETING_WHEN="unknown; release has no built-in pack defaults"
  SYNTHETIC_NEXT='[]'
fi

mkdir -p "$(dirname "$OUT")"
mkdir -p "$REPO_ROOT/$EVIDENCE_ROOT" 2>/dev/null || mkdir -p "$EVIDENCE_ROOT"

ISSUES=()
WORKTREE_EXISTS="false"
ACTUAL_BRANCH=""
HEAD_COMMIT=""
BASE_IS_ANCESTOR="false"
HEAD_EQUALS_BASE="false"
DIRTY="false"
COMMITTED_DIFF_JSON='[]'
DIRTY_DIFF_JSON='[]'
TESTS3_DIFF_JSON='[]'

if [ -d "$WORKTREE/.git" ] || git -C "$WORKTREE" rev-parse --git-dir >/dev/null 2>&1; then
  WORKTREE_EXISTS="true"
  ACTUAL_BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)"
  HEAD_COMMIT="$(git -C "$WORKTREE" rev-parse HEAD)"
  if [ "$ACTUAL_BRANCH" != "$BRANCH" ]; then
    ISSUES+=("branch mismatch: expected $BRANCH got $ACTUAL_BRANCH")
  fi
  if git -C "$WORKTREE" merge-base --is-ancestor "$BASE_COMMIT" HEAD >/dev/null 2>&1; then
    BASE_IS_ANCESTOR="true"
  else
    ISSUES+=("base commit is not an ancestor of pack HEAD")
  fi
  if [ "$HEAD_COMMIT" = "$BASE_COMMIT" ]; then
    HEAD_EQUALS_BASE="true"
  fi
  if [ -n "$(git -C "$WORKTREE" status --porcelain)" ]; then
    DIRTY="true"
  fi
  COMMITTED_DIFF_JSON="$(git -C "$WORKTREE" diff --name-only "$BASE_COMMIT"..HEAD -- . ':!tests3' | json_array_from_lines)"
  DIRTY_DIFF_JSON="$(git -C "$WORKTREE" status --porcelain | json_array_from_lines)"
  TESTS3_DIFF_JSON="$(
    {
      git -C "$WORKTREE" diff --name-only "$BASE_COMMIT"..HEAD -- tests3 2>/dev/null || true
      git -C "$WORKTREE" status --porcelain -- tests3 2>/dev/null || true
    } | json_array_from_lines
  )"
  if [ "$(printf '%s' "$TESTS3_DIFF_JSON" | jq 'length')" -gt 0 ]; then
    ISSUES+=("tests3 is touched; tests3 is deprecated and excluded from release evidence")
  fi
else
  ISSUES+=("worktree missing: $WORKTREE")
fi

ASSIGNED_PORTS=()
[ -n "$LITE_DASHBOARD_PORT" ] && ASSIGNED_PORTS+=("lite_dashboard:$LITE_DASHBOARD_PORT")
[ -n "$LITE_GATEWAY_PORT" ] && ASSIGNED_PORTS+=("lite_gateway:$LITE_GATEWAY_PORT")
[ -n "$COMPOSE_DASHBOARD_PORT" ] && ASSIGNED_PORTS+=("compose_dashboard:$COMPOSE_DASHBOARD_PORT")
[ -n "$COMPOSE_GATEWAY_PORT" ] && ASSIGNED_PORTS+=("compose_gateway:$COMPOSE_GATEWAY_PORT")

PORTS_TMP="$(mktemp)"
for entry in "${ASSIGNED_PORTS[@]}"; do
  label="${entry%%:*}"
  port="${entry##*:}"
  forbidden="false"
  case "$port" in
    3000|8056|8080) forbidden="true"; ISSUES+=("forbidden default/popular port assigned: $label=$port") ;;
  esac
  listeners="$(port_listeners "$port")"
  listening="false"
  if [ -n "$listeners" ]; then
    listening="true"
  fi
  jq -cn \
    --arg port_label "$label" \
    --argjson port "$port" \
    --argjson forbidden "$forbidden" \
    --argjson listening "$listening" \
    --arg listeners "$listeners" \
    '{"label":$port_label, "port":$port, "forbidden":$forbidden, "listening":$listening, "listeners":$listeners}' >> "$PORTS_TMP"
done
PORTS_JSON="$(jq -s '.' "$PORTS_TMP")"
rm -f "$PORTS_TMP"

DOCKER_JSON='[]'
if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
  DOCKER_JSON="$(docker ps --filter "name=$RUNTIME_PREFIX" --format '{{json .}}' | jq -s '.')"
fi

ISSUES_JSON="$(printf '%s\n' "${ISSUES[@]}" | json_array_from_lines)"
STATUS="pass"
if [ "$(printf '%s' "$ISSUES_JSON" | jq 'length')" -gt 0 ]; then
  STATUS="fail"
fi

jq -n \
  --arg release "$RELEASE" \
  --arg pack "$PACK" \
  --arg pack_name "$PACK_NAME" \
  --arg status "$STATUS" \
  --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg worktree "$WORKTREE" \
  --arg expected_branch "$BRANCH" \
  --arg actual_branch "$ACTUAL_BRANCH" \
  --arg base_commit "$BASE_COMMIT" \
  --arg head_commit "$HEAD_COMMIT" \
  --arg runtime_prefix "$RUNTIME_PREFIX" \
  --arg evidence_root "$EVIDENCE_ROOT" \
  --arg real_meeting_when "$REAL_MEETING_WHEN" \
  --argjson worktree_exists "$WORKTREE_EXISTS" \
  --argjson base_is_ancestor "$BASE_IS_ANCESTOR" \
  --argjson head_equals_base "$HEAD_EQUALS_BASE" \
  --argjson dirty "$DIRTY" \
  --argjson real_meeting_needed_now "$REAL_MEETING_NEEDED_NOW" \
  --argjson ports "$PORTS_JSON" \
  --argjson docker_containers "$DOCKER_JSON" \
  --argjson committed_diff "$COMMITTED_DIFF_JSON" \
  --argjson dirty_diff "$DIRTY_DIFF_JSON" \
  --argjson tests3_diff "$TESTS3_DIFF_JSON" \
  --argjson issues "$ISSUES_JSON" \
  --argjson synthetic_next "$SYNTHETIC_NEXT" \
  '{
    release: $release,
    pack: $pack,
    pack_name: $pack_name,
    status: $status,
    generated_at: $generated_at,
    worktree: {
      path: $worktree,
      exists: $worktree_exists,
      expected_branch: $expected_branch,
      actual_branch: (if $actual_branch == "" then null else $actual_branch end),
      base_commit: $base_commit,
      head_commit: (if $head_commit == "" then null else $head_commit end),
      base_is_ancestor: $base_is_ancestor,
      head_equals_base: $head_equals_base,
      dirty: $dirty,
      committed_diff_files: $committed_diff,
      dirty_files: $dirty_diff,
      tests3_touched: $tests3_diff
    },
    runtime: {
      prefix: $runtime_prefix,
      ports: $ports,
      docker_containers: $docker_containers
    },
    evidence_root: $evidence_root,
    synthetic_first: {
      real_meeting_needed_now: $real_meeting_needed_now,
      real_meeting_when: $real_meeting_when,
      next_synthetic_actions: $synthetic_next
    },
    issues: $issues
  }' > "$OUT"

jq '.status as $status | {status:$status, pack, pack_name, real_meeting_needed_now:.synthetic_first.real_meeting_needed_now, issues}' "$OUT"

[ "$STATUS" = "pass" ]
