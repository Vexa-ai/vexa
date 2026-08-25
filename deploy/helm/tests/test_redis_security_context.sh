#!/usr/bin/env bash
set -euo pipefail

HELM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$HELM_DIR/charts/vexa"

if ! command -v helm >/dev/null 2>&1; then
  echo "SKIP: helm not installed"
  exit 0
fi

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== gate:helm-redis-security-context ==="

empty="$(helm template vexa "$CHART" -n vexa --show-only templates/deployment-redis.yaml \
  --set global.podSecurityContext=null \
  --set global.securityContext=null)"
if grep -q 'securityContext:' <<<"$empty"; then
  fail "empty global security contexts changed the Redis manifest"
fi
echo "  OK: empty global contexts preserve the Redis manifest"

rendered="$(helm template vexa "$CHART" -n vexa --show-only templates/deployment-redis.yaml \
  --set global.podSecurityContext.runAsNonRoot=true \
  --set global.podSecurityContext.seccompProfile.type=RuntimeDefault \
  --set global.securityContext.allowPrivilegeEscalation=false \
  --set global.securityContext.runAsNonRoot=true \
  --set global.securityContext.capabilities.drop[0]=ALL)"

pod_spec="$(sed -n '/^    spec:/,/^      containers:/p' <<<"$rendered")"
container_spec="$(sed -n '/^        - name: redis$/,/^      volumes:/p' <<<"$rendered")"

grep -q '^      securityContext:$' <<<"$pod_spec" || fail "Redis Pod is missing global.podSecurityContext"
grep -q '^        runAsNonRoot: true$' <<<"$pod_spec" || fail "Redis Pod is missing runAsNonRoot"
grep -q '^          type: RuntimeDefault$' <<<"$pod_spec" || fail "Redis Pod is missing seccompProfile"
grep -q '^          securityContext:$' <<<"$container_spec" || fail "Redis container is missing global.securityContext"
grep -q '^            allowPrivilegeEscalation: false$' <<<"$container_spec" || fail "Redis container is missing allowPrivilegeEscalation"
grep -q '^            runAsNonRoot: true$' <<<"$container_spec" || fail "Redis container is missing runAsNonRoot"
grep -q '^              - ALL$' <<<"$container_spec" || fail "Redis container is missing dropped capabilities"

echo "  OK: Redis consumes the chart-wide restricted security contexts"
echo "gate:helm-redis-security-context PASS"
