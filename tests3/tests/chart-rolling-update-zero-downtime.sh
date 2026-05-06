#!/usr/bin/env bash
# chart-rolling-update-zero-downtime — v0.10.5.3 Pack H regression guard.
#
# Every app-facing Deployment in the rendered chart must set
# strategy.rollingUpdate.maxUnavailable: 0 (via the vexa.deploymentStrategy
# helper). This ensures the OLD pod stays Ready until the NEW pod is Ready,
# preventing the v0.10.5.2-class outage where dashboard + webapp went 502
# during a routine image bump because maxUnavailable: 1 + replicaCount: 1
# killed the OLD pod before the NEW pod could pull its image.
#
# Services with their own Recreate strategy (redis, tts-service, minio)
# are exempt — their volumes can't be shared across pods.
#
# History: pre-v0.10.5.3 the check enforced maxSurge: 0 (to prevent DB
# pool footprint doubling during rolling). Pack H supersedes that with
# maxUnavailable: 0 (zero-downtime). The DB pool concern is mitigated by
# v0.10.5 Pack C.x's per-pod pool shrink (8 -> 2 connections per holder),
# so 2x footprint during rolling is well below the managed-DB slot cap.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"

echo ""
echo "  chart-rolling-update-zero-downtime"
echo "  ──────────────────────────────────────────────"

test_begin chart-rolling-update-zero-downtime

rendered=$(helm template vexa "$CHART_DIR" 2>/dev/null)

result=$(echo "$rendered" | python3 -c "
import sys, re
txt = sys.stdin.read()
# Services that legitimately use Recreate strategy.
RECREATE_SERVICES = {'redis', 'tts-service', 'minio'}
blocks = txt.split('---')
bad = []
ok = []
for b in blocks:
    if 'kind: Deployment' not in b:
        continue
    m = re.search(r'component:\s*(\S+)', b)
    if not m:
        continue
    comp = m.group(1)
    if comp in RECREATE_SERVICES:
        if 'type: Recreate' in b or re.search(r'maxUnavailable:\s*0', b):
            ok.append(comp)
        else:
            bad.append(f'{comp}:no-strategy')
    else:
        if re.search(r'maxUnavailable:\s*0', b):
            ok.append(comp)
        else:
            bad.append(f'{comp}:missing-maxUnavailable-0')

if bad:
    print('FAIL: ' + ' '.join(bad))
else:
    print(f'OK: {len(ok)} Deployments — {sorted(set(ok))}')
")

if echo "$result" | grep -q '^OK'; then
    step_pass HELM_ROLLING_UPDATE_ZERO_DOWNTIME "${result#OK: }"
else
    step_fail HELM_ROLLING_UPDATE_ZERO_DOWNTIME "${result#FAIL: }"
fi

echo "  ──────────────────────────────────────────────"
echo ""
