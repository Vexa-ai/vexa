#!/usr/bin/env bash
# chart-rolling-update-zero-surge — Pack G regression guard.
#
# Every app-facing Deployment in the rendered chart must set
# strategy.rollingUpdate.maxSurge: 0 (via the vexa.deploymentStrategy
# helper). Services with their own Recreate strategy (redis, tts-service)
# are exempt — their volumes can't be shared across pods.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"

echo ""
echo "  chart-rolling-update-zero-surge"
echo "  ──────────────────────────────────────────────"

test_begin chart-rolling-update-zero-surge

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
    # Extract component name
    m = re.search(r'component:\s*(\S+)', b)
    if not m:
        continue
    comp = m.group(1)
    if comp in RECREATE_SERVICES:
        # Must have Recreate strategy or our zero-surge helper — either
        # satisfies the pool-overlap guarantee.
        if 'type: Recreate' in b or re.search(r'maxSurge:\s*0', b):
            ok.append(comp)
        else:
            bad.append(f'{comp}:no-strategy')
    else:
        if re.search(r'maxSurge:\s*0', b):
            ok.append(comp)
        else:
            bad.append(f'{comp}:missing-maxSurge-0')

if bad:
    print('FAIL: ' + ' '.join(bad))
else:
    print(f'OK: {len(ok)} Deployments — {sorted(set(ok))}')
")

if echo "$result" | grep -q '^OK'; then
    step_pass HELM_ROLLING_UPDATE_ZERO_SURGE "${result#OK: }"
else
    step_fail HELM_ROLLING_UPDATE_ZERO_SURGE "${result#FAIL: }"
fi

echo "  ──────────────────────────────────────────────"
echo ""
