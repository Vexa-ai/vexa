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

: "${BASE:=http://localhost:8056}"
: "${MODE:=local}"
: "${REPORT_DIR:=$(dirname "$0")/../.state-${MODE}/reports/${MODE}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="$SCRIPT_DIR/scenarios"

mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/synthetic.json"

declare -a steps=()
overall_status=pass
ts_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
total_ms=0

# Verify endpoint is reachable. Use /admin/users probe (a real endpoint
# served by admin-api through the gateway) — the gateway has no generic
# /health route, only per-service ones.
: "${ADMIN_TOKEN:=changeme}"
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

for scenario in "$SCENARIOS_DIR"/*.sh; do
    [ -f "$scenario" ] || continue
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

    msg=$(tail -1 "/tmp/scenario-$name.log" 2>/dev/null | tr '"' "'" || echo "")
    # Map upper-case for step id consistency with other static checks
    step_id=$(echo "PACK_X_$name" | tr 'a-z-' 'A-Z_')
    steps+=("{\"id\":\"$step_id\",\"status\":\"$verdict\",\"message\":\"$msg\",\"duration_ms\":$ms}")
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
