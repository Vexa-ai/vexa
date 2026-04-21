#!/usr/bin/env bash
# chart-all-services-db-pool-tuned — Pack D.2 static regression guard.
#
# Every pool-holder service's chart Deployment must set DB_POOL_SIZE env
# explicitly (not rely on silent SQLAlchemy / asyncpg framework defaults).
# Prevents the sum-over-cap risk identified in incident-doc §4.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"

echo ""
echo "  chart-all-services-db-pool-tuned"
echo "  ──────────────────────────────────────────────"

test_begin chart-all-services-db-pool-tuned

# Pool-holder services — each talks to Postgres directly (via SQLAlchemy /
# asyncpg). api-gateway proxies only and holds no pool of its own.
# Broadening here when the chart grows a new DB-connected service means
# adding the service name below.
SERVICES="admin-api meeting-api runtime-api"

missing=""
rendered=$(helm template vexa "$CHART_DIR" 2>/dev/null)

for svc in $SERVICES; do
    # Extract the Deployment for this service and look for DB_POOL_SIZE env.
    # `yq` is too heavy for a tests3 step; use python one-liner.
    has_pool_size=$(echo "$rendered" | python3 -c "
import sys, re
svc = '$svc'
txt = sys.stdin.read()
# Pick out the Deployment YAML document for this component
blocks = txt.split('---')
for b in blocks:
    if 'kind: Deployment' not in b:
        continue
    if f'component: {svc}' not in b:
        continue
    # Found this service's Deployment. Does it set DB_POOL_SIZE?
    if re.search(r'name:\s*DB_POOL_SIZE', b):
        print('yes')
        sys.exit(0)
print('no')
")
    if [ "$has_pool_size" != "yes" ]; then
        missing+=" $svc"
    fi
done

if [ -z "$missing" ]; then
    step_pass all_services_db_pool_tuned "every pool-holder service declares DB_POOL_SIZE env (admin-api, meeting-api, runtime-api)"
else
    step_fail all_services_db_pool_tuned "services without explicit DB_POOL_SIZE env:$missing"
fi

echo "  ──────────────────────────────────────────────"
echo ""
