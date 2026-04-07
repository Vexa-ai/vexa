#!/usr/bin/env bash
# Auto-detect deployment mode and URLs. Writes results to .state/.
source "$(dirname "$0")/common.sh"

MODE=$(detect_mode)

if [ "$MODE" = "none" ]; then
    echo "$(red "ERROR"): No deployment found (no compose, no lite container, no k8s)."
    exit 1
fi

detect_urls "$MODE"

state_write deploy_mode "$MODE"
state_write gateway_url "$GATEWAY_URL"
state_write admin_url "$ADMIN_URL"
state_write dashboard_url "$DASHBOARD_URL"

echo "  $(dim "mode=$MODE  gw=$GATEWAY_URL  dash=$DASHBOARD_URL")"
