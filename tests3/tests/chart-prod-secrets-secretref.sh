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
    # Render chart with default values (postgres.enabled=true) and scan
    # every Deployment env entry for the secret names. Each must appear as
    # `valueFrom: secretKeyRef:` — NOT as plain `value:`.
    rendered=$(helm template vexa "$CHART_DIR" 2>&1) || {
        step_fail secretref_only "helm template failed: ${rendered:0:200}"
        [ "$STEP_REQUESTED" = "secretref_only" ] && exit 1
    }

    bad=""
    for secret in DB_PASSWORD TRANSCRIPTION_SERVICE_TOKEN; do
        # Match lines: "- name: DB_PASSWORD" followed within ~4 lines by
        # either `value:` (BAD) or `valueFrom:` (GOOD). Use Python for reliable multiline parsing.
        if echo "$rendered" | python3 -c "
import sys, re
txt = sys.stdin.read()
secret = '$secret'
# Find every occurrence of '- name: <SECRET>' and look ahead 4 lines.
for m in re.finditer(r'- name: ' + re.escape(secret) + r'\s*\n((?:\s{12,}.+\n){1,4})', txt):
    block = m.group(1)
    # Plain value branch: matches `              value:` with something after
    if re.search(r'^\s{14,}value:\s*\S', block, re.MULTILINE):
        print('plain value seen')
        sys.exit(1)
    if 'valueFrom' not in block and 'secretKeyRef' not in block:
        print('no secretKeyRef')
        sys.exit(2)
sys.exit(0)
" 2>/dev/null; then
            :
        else
            bad+=" $secret"
        fi
    done

    if [ -z "$bad" ]; then
        step_pass secretref_only "DB_PASSWORD + TRANSCRIPTION_SERVICE_TOKEN rendered via secretKeyRef in every Deployment"
    else
        step_fail secretref_only "plain value: detected for:$bad"
    fi
fi

# ── Step: required_at_render ──────────────────────────────────
if [ -z "$STEP_REQUESTED" ] || [ "$STEP_REQUESTED" = "required_at_render" ]; then
    # External-DB mode with empty credentialsSecretName — chart MUST fail
    # with a `required` error, not silently render.
    if helm template vexa "$CHART_DIR" \
        --set postgres.enabled=false \
        --set database.host=ext.example.com \
        --set postgres.credentialsSecretName= \
        2>&1 | grep -qi "required"; then
        step_pass required_at_render "helm template fails with 'required' on missing credentialsSecretName"
    else
        step_fail required_at_render "helm template did not surface a 'required' error for missing credentialsSecretName"
    fi
fi

echo "  ──────────────────────────────────────────────"
echo ""
