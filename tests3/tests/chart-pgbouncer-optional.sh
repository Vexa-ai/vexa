#!/usr/bin/env bash
# chart-pgbouncer-optional — Pack H regression guard.
#
# Default render: no pgbouncer Deployment or Service.
# `--set pgbouncer.enabled=true` render: Deployment + Service exist,
# and every app service's DB_HOST rewires to the pgbouncer Service.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"

echo ""
echo "  chart-pgbouncer-optional"
echo "  ──────────────────────────────────────────────"

test_begin chart-pgbouncer-optional

# Default render — pgbouncer disabled. grep -c may exit 1 when zero matches,
# and `set -e` from common.sh would kill us; || echo 0 is the guard.
default_rendered=$(helm template vexa "$CHART_DIR" 2>&1)
default_has_pgbouncer=$({ echo "$default_rendered" | grep -c 'component: pgbouncer'; } || true)
[ -z "$default_has_pgbouncer" ] && default_has_pgbouncer=0

if [ "$default_has_pgbouncer" -eq 0 ]; then
    step_pass default_off "default render has no pgbouncer Deployment/Service"
else
    step_fail default_off "default render unexpectedly includes pgbouncer (count=$default_has_pgbouncer)"
fi

# Enabled render — verify Deployment + Service + DB_HOST rewire
enabled_rendered=$(helm template vexa "$CHART_DIR" --set pgbouncer.enabled=true 2>&1)
enabled_has_pgbouncer=$({ echo "$enabled_rendered" | grep -c 'component: pgbouncer'; } || true)
[ -z "$enabled_has_pgbouncer" ] && enabled_has_pgbouncer=0

if [ "$enabled_has_pgbouncer" -lt 2 ]; then
    step_fail enabled_renders "pgbouncer.enabled=true render missing Deployment+Service (count=$enabled_has_pgbouncer)"
else
    step_pass enabled_renders "pgbouncer.enabled=true renders Deployment + Service (count=$enabled_has_pgbouncer)"
fi

# Every service's DB_HOST points at pgbouncer when enabled — except
# pgbouncer's own Deployment, which must still point at the real postgres.
rewired_check=$(echo "$enabled_rendered" | python3 -c "
import sys, re
txt = sys.stdin.read()
blocks = txt.split('---')
rewired_correctly = []
rewired_wrong = []
pgbouncer_direct = False
for b in blocks:
    if 'kind: Deployment' not in b:
        continue
    cm = re.search(r'component:\s*(\S+)', b)
    if not cm: continue
    comp = cm.group(1)
    # DB_HOST value lines — allow comment lines between name: and value:
    # (the pgbouncer template has explanatory comments there).
    m = re.search(r'name: DB_HOST\s*\n(?:\s*#[^\n]*\n)*\s*value:\s*\"([^\"]+)\"', b)
    if not m:
        continue
    db_host = m.group(1)
    if comp == 'pgbouncer':
        # pgbouncer's own DB_HOST must NOT point at pgbouncer (would be a loop)
        if 'pgbouncer' in db_host:
            rewired_wrong.append(f'pgbouncer-self:{db_host}')
        else:
            pgbouncer_direct = True
    else:
        if 'pgbouncer' in db_host:
            rewired_correctly.append(comp)
        else:
            rewired_wrong.append(f'{comp}:{db_host}')
if rewired_wrong:
    print('FAIL: ' + ' '.join(rewired_wrong))
else:
    print(f'OK: rewired={sorted(set(rewired_correctly))} pgbouncer_self_routed_to_postgres={pgbouncer_direct}')
")

if echo "$rewired_check" | grep -q '^OK'; then
    step_pass db_host_rewired "${rewired_check#OK: }"
else
    step_fail db_host_rewired "${rewired_check#FAIL: }"
fi

# Rollup step — scope.yaml binds the check ID HELM_PGBOUNCER_OPTIONAL_AND_WIRED
# which covers all three sub-conditions. Pass only if every sub-step passed.
if [ "$default_has_pgbouncer" -eq 0 ] && [ "$enabled_has_pgbouncer" -ge 2 ] && echo "$rewired_check" | grep -q '^OK'; then
    step_pass HELM_PGBOUNCER_OPTIONAL_AND_WIRED "pgbouncer optional subchart + DB_HOST rewire contract holds"
else
    step_fail HELM_PGBOUNCER_OPTIONAL_AND_WIRED "pgbouncer subchart contract not fully satisfied (see sub-steps above)"
fi

echo "  ──────────────────────────────────────────────"
echo ""
