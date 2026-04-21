#!/usr/bin/env bash
# chart-prod-secrets-secretref — Pack A regression guard.
#
# Step IDs (stable — bound to registry.yaml):
#   secretref_only       — every prod-critical env in rendered chart uses secretKeyRef
#   required_at_render   — helm template fails when secret material is missing
#
# Static / render-time only. Needs `helm` on PATH (already present in the
# tests3 base image).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"
STEP_REQUESTED="${1:-}"

if ! command -v helm >/dev/null 2>&1; then
    echo "  helm not on PATH; skipping chart-prod-secrets-secretref"
    exit 0
fi

echo ""
echo "  chart-prod-secrets-secretref"
echo "  ──────────────────────────────────────────────"

test_begin chart-prod-secrets-secretref

# ── Step: secretref_only ───────────────────────────────────────
if [ -z "$STEP_REQUESTED" ] || [ "$STEP_REQUESTED" = "secretref_only" ]; then
    rendered=$(helm template vexa "$CHART_DIR" 2>&1) || {
        step_fail secretref_only "helm template failed: ${rendered:0:200}"
        [ "$STEP_REQUESTED" = "secretref_only" ] && exit 1
    }

    set +e
    bad=$(echo "$rendered" | python3 - <<'PY'
import sys, re
txt = sys.stdin.read()
bad = []
for secret in ("DB_PASSWORD", "TRANSCRIPTION_SERVICE_TOKEN"):
    for m in re.finditer(r'- name: ' + re.escape(secret) + r'\s*\n((?:\s{12,}.+\n){1,4})', txt):
        block = m.group(1)
        if re.search(r'^\s{14,}value:\s*\S', block, re.MULTILINE):
            bad.append(secret + ':plain-value')
            break
        if 'valueFrom' not in block and 'secretKeyRef' not in block:
            bad.append(secret + ':no-secretKeyRef')
            break
print(' '.join(bad))
PY
)
    set -e

    if [ -z "$bad" ]; then
        step_pass secretref_only "DB_PASSWORD + TRANSCRIPTION_SERVICE_TOKEN rendered via secretKeyRef in every Deployment"
    else
        step_fail secretref_only "plain value: detected for: $bad"
    fi
fi

# ── Step: required_at_render ──────────────────────────────────
if [ -z "$STEP_REQUESTED" ] || [ "$STEP_REQUESTED" = "required_at_render" ]; then
    # External-DB mode with empty credentialsSecretName — chart MUST fail
    # with the `required` directive's error message, not silently render.
    # `|| true` because helm template is expected to exit non-zero here; we
    # grade on the message content, not the exit code.
    out=$(helm template vexa "$CHART_DIR" \
        --set postgres.enabled=false \
        --set database.host=ext.example.com \
        --set postgres.credentialsSecretName= \
        2>&1 || true)
    if echo "$out" | grep -qE "execution error.*credentialsSecretName|must name a pre-existing Secret"; then
        step_pass required_at_render "helm template fails with the required-directive error on missing credentialsSecretName"
    else
        step_fail required_at_render "helm template did not fail as expected (out tail: ${out: -200})"
    fi
fi

echo "  ──────────────────────────────────────────────"
echo ""
