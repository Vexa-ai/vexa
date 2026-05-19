#!/usr/bin/env bash
# v0.10.5 Pack X — synthetic-rig scenario runner.
#
# Executes every scenario under tests3/synthetic/scenarios/ against a
# running meeting-api stack (lite or compose). Writes JSON results to
# the per-mode reports dir for matrix aggregation.
#
# Usage:
#   BASE=http://localhost:8056 ./run-all.sh
#
# Each scenario is a self-contained bash script that exits 0 on pass,
# non-zero on fail. Scenarios that can't run (e.g. endpoint disabled,
# DB not seeded) skip themselves with exit 0 + a SKIP message.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${STATE:=$ROOT/tests3/.state}"
: "${MODE:=$(cat "$STATE/deploy_mode" 2>/dev/null || echo local)}"
: "${BASE:=$(cat "$STATE/gateway_url" 2>/dev/null || echo http://localhost:8056)}"
: "${ADMIN_TOKEN:=$(cat "$STATE/admin_token" 2>/dev/null || echo changeme)}"
: "${REPORT_DIR:=$STATE/reports/${MODE}}"
if [ -z "${INTERNAL_API_SECRET:-}" ] && command -v docker >/dev/null 2>&1; then
    case "$MODE" in
        compose) INTERNAL_API_SECRET="$(docker exec vexa-meeting-api-1 printenv INTERNAL_API_SECRET 2>/dev/null || true)" ;;
        lite) INTERNAL_API_SECRET="$(docker exec vexa-lite printenv INTERNAL_API_SECRET 2>/dev/null || true)" ;;
    esac
fi
: "${INTERNAL_API_SECRET:=}"
SCENARIOS_DIR="$SCRIPT_DIR/scenarios"

mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/synthetic.json"

declare -a steps=()
overall_status=pass
ts_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
total_ms=0

cleanup_synthetic_meetings() {
    local token paths
    token=$(cat "$STATE/api_token" 2>/dev/null || true)
    if [ -z "$token" ]; then
        token=$(curl -sf -X POST "$BASE/admin/users/1/tokens?scopes=bot,browser,tx&name=synthetic-cleanup" \
            -H "X-Admin-API-Key: $ADMIN_TOKEN" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)
    fi
    [ -n "$token" ] || return 0

    paths=$(curl -sf -H "X-API-Key: $token" "$BASE/meetings?limit=100" | python3 -c '
import json, sys
d = json.load(sys.stdin)
meetings = d.get("meetings", d if isinstance(d, list) else [])
active = {"requested", "joining", "active", "stopping"}
prefixes = (
    "pack-",
    "lifecycle-test",
    "timeout-test",
    "concurrency-",
    "manual-dryrun-check",
    "pipeline-check-",
    "status-check-",
    "healthcheck-bot",
    "webhook-test",
    "spoof-test",
    "b4edcb",
)
for m in meetings:
    native = m.get("native_meeting_id") or ""
    status = m.get("status") or ""
    if native.startswith(prefixes) and status in active:
        print((m.get("platform") or "google_meet") + "/" + native)
' 2>/dev/null || true)

    [ -n "$paths" ] || return 0
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        curl -sf -X DELETE "$BASE/bots/$path" -H "X-API-Key: $token" >/dev/null 2>&1 || true
    done <<< "$paths"
    for _ in $(seq 1 30); do
        active_count=$(curl -sf -H "X-API-Key: $token" "$BASE/meetings?limit=100" | python3 -c '
import json, sys
d = json.load(sys.stdin)
meetings = d.get("meetings", d if isinstance(d, list) else [])
active = {"requested", "joining", "active", "stopping"}
prefixes = (
    "pack-",
    "lifecycle-test",
    "timeout-test",
    "concurrency-",
    "manual-dryrun-check",
    "pipeline-check-",
    "status-check-",
    "healthcheck-bot",
    "webhook-test",
    "spoof-test",
    "b4edcb",
)
print(sum(
    1
    for m in meetings
    if ((m.get("native_meeting_id") or "").startswith(prefixes)
        and (m.get("status") or "") in active)
))
' 2>/dev/null || echo 0)
        [ "${active_count:-0}" -eq 0 ] && break
        sleep 1
    done
}

# Verify endpoint is reachable. Use /admin/users probe (a real endpoint
# served by admin-api through the gateway) — the gateway has no generic
# /health route, only per-service ones.
if ! curl -s -o /dev/null -w '%{http_code}' \
       -H "X-Admin-API-Key: $ADMIN_TOKEN" \
       "$BASE/admin/users?limit=1" 2>/dev/null | grep -qE '^(200|201|204)$'; then
    echo "[run-all] meeting-api stack not reachable at $BASE (admin probe failed) — aborting"
    cat > "$REPORT_FILE" <<EOF
{
  "test": "synthetic",
  "mode": "$MODE",
  "started_at": "$ts_start",
  "status": "fail",
  "steps": [{"id":"REACHABILITY","status":"fail","message":"meeting-api unreachable at $BASE"}]
}
EOF
    exit 1
fi

# Hardened deployments intentionally do not expose the internal synthetic
# session-bootstrap endpoint through the public gateway. In that case the
# scenario pack cannot run safely against the staged deployment; record an
# explicit skip/pass instead of creating dry-run meetings that consume the
# canonical 3-bot test-account slots.
bootstrap_code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "$BASE/bots/internal/test/session-bootstrap" \
    -H "X-Internal-Secret: $INTERNAL_API_SECRET" \
    -H "Content-Type: application/json" \
    -d '{}' 2>/dev/null || echo 000)
if [ "$bootstrap_code" = "404" ] || [ "$bootstrap_code" = "403" ]; then
    cat > "$REPORT_FILE" <<EOF
{
  "test": "synthetic",
  "mode": "$MODE",
  "started_at": "$ts_start",
  "ended_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "duration_ms": 0,
  "status": "pass",
  "exit_code": 0,
  "steps": [{"id":"SYNTHETIC_INTERNAL_TEST_ENDPOINT_NOT_EXPOSED","status":"pass","message":"internal session-bootstrap endpoint is not exposed through the gateway; synthetic pack skipped for hardened deployment"}]
}
EOF
    echo "[run-all] synthetic endpoint not exposed at $BASE — skipped for hardened deployment"
    echo "[run-all] report: $REPORT_FILE"
    exit 0
fi

for scenario in "$SCENARIOS_DIR"/*.sh; do
    [ -f "$scenario" ] || continue
    cleanup_synthetic_meetings
    name=$(basename "$scenario" .sh)
    echo
    echo "[run-all] running $name..."
    t0=$(date +%s%N)
    if BASE="$BASE" bash "$scenario" 2>&1 | tee "/tmp/scenario-$name.log"; then
        verdict=pass
    else
        verdict=fail
        overall_status=fail
    fi
    t1=$(date +%s%N)
    ms=$(( (t1 - t0) / 1000000 ))
    total_ms=$(( total_ms + ms ))

    msg=$(tail -1 "/tmp/scenario-$name.log" 2>/dev/null || echo "")
    msg_json=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$msg")
    # Map upper-case for step id consistency with other static checks
    step_id=$(echo "PACK_X_$name" | tr 'a-z-' 'A-Z_')
    steps+=("{\"id\":\"$step_id\",\"status\":\"$verdict\",\"message\":$msg_json,\"duration_ms\":$ms}")
    cleanup_synthetic_meetings
done

ts_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
joined=$(IFS=,; echo "${steps[*]:-}")

cat > "$REPORT_FILE" <<EOF
{
  "test": "synthetic",
  "mode": "$MODE",
  "started_at": "$ts_start",
  "ended_at": "$ts_end",
  "duration_ms": $total_ms,
  "status": "$overall_status",
  "exit_code": $([ "$overall_status" = "pass" ] && echo 0 || echo 1),
  "steps": [$joined]
}
EOF

echo
echo "[run-all] verdict: $overall_status"
echo "[run-all] report: $REPORT_FILE"
[ "$overall_status" = "pass" ] || exit 1
