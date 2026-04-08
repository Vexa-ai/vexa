#!/usr/bin/env bash
# Auto-detect deployment mode and URLs. Writes results to .state/.
source "$(dirname "$0")/common.sh"

# If DEPLOY_MODE is explicitly set, use it. Otherwise auto-detect.
# Honor existing state for helm (avoids local lite overriding remote cluster).
if [ "${DEPLOY_MODE:-auto}" != "auto" ]; then
    MODE="$DEPLOY_MODE"
elif state_exists deploy_mode && [ "$(cat "$STATE/deploy_mode")" = "helm" ] && state_exists lke_kubeconfig_path; then
    MODE="helm"
else
    MODE=$(detect_mode)
fi

if [ "$MODE" = "none" ]; then
    echo "$(red "ERROR"): No deployment found (no compose, no lite container, no k8s)."
    exit 1
fi

detect_urls "$MODE"

state_write deploy_mode "$MODE"
state_write gateway_url "$GATEWAY_URL"
state_write admin_url "$ADMIN_URL"
state_write dashboard_url "$DASHBOARD_URL"

# Helm: detect release name for deployment name construction
if [ "$MODE" = "helm" ]; then
    HELM_RELEASE=$(kubectl get deploy -l app.kubernetes.io/name=vexa \
        -o jsonpath='{.items[0].metadata.labels.app\.kubernetes\.io/instance}' 2>/dev/null || echo "")
    if [ -n "$HELM_RELEASE" ]; then
        state_write helm_release "$HELM_RELEASE"
        echo "  $(dim "helm_release=$HELM_RELEASE")"
    fi
fi

echo "  $(dim "mode=$MODE  gw=$GATEWAY_URL  dash=$DASHBOARD_URL")"
