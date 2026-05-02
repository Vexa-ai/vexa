#!/usr/bin/env bash
# autonomous-real-meeting.sh — drive the comprehensive harness across all
# (deployment × platform) cells you have meeting URLs for, in parallel.
#
# Usage:
#   tests3/tests/autonomous-real-meeting.sh \
#     --gmeet=https://meet.google.com/abc-defg-hij \
#     --teams=https://teams.microsoft.com/meet/<id>?p=<passcode> \
#     --zoom=https://us04web.zoom.us/j/<id>?pwd=<passcode> \
#     [--deployments=compose,helm,lite]   # default: all 3
#     [--mode=normal|crash]               # default: normal
#     [--duration=240]                    # seconds to record
#
# The script dispatches one bot per (platform, deployment) cell. All bots
# join the SAME meeting URL — admit each in the host UI as they appear.
# Each per-cell python invocation runs independently and writes its own
# JSON report; this wrapper aggregates the verdicts at the end.
#
# Exit code: 0 if every cell passed, 1 otherwise.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/tests3/tests/autonomous_real_meeting.py"

GMEET_URL=""
TEAMS_URL=""
ZOOM_URL=""
DEPLOYMENTS="compose,helm,lite"
MODE="normal"
DURATION=240

for arg in "$@"; do
    case "$arg" in
        --gmeet=*)        GMEET_URL="${arg#*=}" ;;
        --teams=*)        TEAMS_URL="${arg#*=}" ;;
        --zoom=*)         ZOOM_URL="${arg#*=}" ;;
        --deployments=*)  DEPLOYMENTS="${arg#*=}" ;;
        --mode=*)         MODE="${arg#*=}" ;;
        --duration=*)     DURATION="${arg#*=}" ;;
        -h|--help)
            grep '^# ' "$0" | sed 's/^# //'
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [ -z "$GMEET_URL$TEAMS_URL$ZOOM_URL" ]; then
    echo "ERROR: at least one of --gmeet / --teams / --zoom must be provided" >&2
    exit 2
fi

ts="$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$ROOT/tests3/.state/reports/auto-real-$ts"
mkdir -p "$RUN_DIR"
echo "  run dir: $RUN_DIR"
echo "  mode=$MODE duration=${DURATION}s"
echo

# build the cell list: (platform, url, deployment)
declare -a PIDS=()
declare -a CELLS=()
declare -a OUTS=()

dispatch_cell() {
    local platform="$1" url="$2" deployment="$3"
    local out="$RUN_DIR/${deployment}-${platform}-${MODE}.json"
    local log="$RUN_DIR/${deployment}-${platform}-${MODE}.log"
    echo "  ▶ ${deployment}/${platform}  → bot dispatching, log=$log"
    (
        python3 "$PY" \
            --platform="$platform" \
            --url="$url" \
            --deployment="$deployment" \
            --mode="$MODE" \
            --duration="$DURATION" \
            --bot-name="t3-${deployment:0:1}-${platform:0:5}" \
            --output="$out" \
            > "$log" 2>&1
    ) &
    PIDS+=("$!")
    CELLS+=("${deployment}/${platform}")
    OUTS+=("$out")
}

IFS=',' read -ra DEPLOY_LIST <<< "$DEPLOYMENTS"
for dep in "${DEPLOY_LIST[@]}"; do
    [ -n "$GMEET_URL" ] && dispatch_cell gmeet    "$GMEET_URL" "$dep"
    [ -n "$TEAMS_URL" ] && dispatch_cell teams    "$TEAMS_URL" "$dep"
    [ -n "$ZOOM_URL" ]  && dispatch_cell zoom_web "$ZOOM_URL"  "$dep"
done

echo
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ${#PIDS[@]} cells dispatched. Admit each bot in the host UI"
echo "  as it appears. Each cell waits up to 180s for status=active."
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

declare -a STATUSES=()
for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    cell="${CELLS[$i]}"
    if wait "$pid"; then
        STATUSES+=("pass")
        echo "  ✓ $cell  pass"
    else
        STATUSES+=("fail")
        echo "  ✗ $cell  fail (rc=$?)"
    fi
done

# aggregate
echo
echo "  ━━━ summary ━━━"
pass=0; fail=0
for i in "${!CELLS[@]}"; do
    cell="${CELLS[$i]}"
    s="${STATUSES[$i]}"
    out="${OUTS[$i]}"
    if [ -f "$out" ]; then
        v=$(python3 -c "import json,sys; print(json.load(open('$out')).get('verdict','?'))")
        af=$(python3 -c "
import json, sys
r = json.load(open('$out'))
p = sum(1 for a in r['assertions'] if a['status']=='pass')
f = sum(1 for a in r['assertions'] if a['status']=='fail')
sk = sum(1 for a in r['assertions'] if a['status']=='skip')
print(f'{p}/{f}/{sk}')
")
        echo "    $cell  $s  ($v, P/F/S=$af)"
    else
        echo "    $cell  $s  (no report file)"
    fi
    [ "$s" = "pass" ] && pass=$((pass+1)) || fail=$((fail+1))
done

echo
echo "  PASSED: $pass / FAILED: $fail"

# write aggregate
agg="$RUN_DIR/aggregate.json"
python3 -c "
import json, glob, os
agg = {'run_dir': '$RUN_DIR', 'mode': '$MODE', 'duration_sec': $DURATION, 'cells': []}
for f in sorted(glob.glob('$RUN_DIR/*-*.json')):
    if f.endswith('aggregate.json'): continue
    try:
        agg['cells'].append(json.load(open(f)))
    except Exception as e:
        agg['cells'].append({'file': f, 'error': str(e)})
agg['verdict'] = 'pass' if all(c.get('verdict')=='pass' for c in agg['cells']) else 'fail'
json.dump(agg, open('$agg', 'w'), indent=2, default=str)
print(f'  aggregate → $agg')
"

if [ "$fail" -gt 0 ]; then
    exit 1
fi
exit 0
