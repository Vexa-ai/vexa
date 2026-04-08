#!/usr/bin/env bash
# LKE cluster lifecycle: provision, wait, kubeconfig, destroy.
# Usage: lib/lke.sh provision | wait | destroy
set -euo pipefail
source "$(dirname "$0")/common.sh"

LINODE_CLI="${LINODE_CLI:-/home/dima/anaconda3/bin/linode-cli}"
LKE_VERSION="${LKE_VERSION:-1.34}"
LKE_REGION="${LKE_REGION:-us-ord}"
LKE_NODE_TYPE="${LKE_NODE_TYPE:-g6-standard-2}"
LKE_NODE_COUNT="${LKE_NODE_COUNT:-2}"

lke_check_prereqs() {
    if ! command -v "$LINODE_CLI" &>/dev/null; then
        fail "linode-cli not found at $LINODE_CLI"
        exit 1
    fi
    if ! command -v helm &>/dev/null; then
        fail "helm not installed"
        exit 1
    fi
    if ! command -v kubectl &>/dev/null; then
        fail "kubectl not installed"
        exit 1
    fi
}

lke_provision() {
    if state_exists lke_id; then
        local existing_id
        existing_id=$(state_read lke_id)
        info "LKE cluster already exists (id=$existing_id)"
        info "run 'make -C tests3 lke-destroy' first, or reuse"
        return 0
    fi

    lke_check_prereqs

    local label="vexa-t3-$(date +%H%M)"

    echo ""
    echo "  lke-provision"
    echo "  ──────────────────────────────────────────────"
    info "creating $label (k8s $LKE_VERSION, ${LKE_NODE_COUNT}x $LKE_NODE_TYPE, $LKE_REGION)..."

    local result
    result=$("$LINODE_CLI" lke cluster-create \
        --label "$label" \
        --region "$LKE_REGION" \
        --k8s_version "$LKE_VERSION" \
        --node_pools.type "$LKE_NODE_TYPE" \
        --node_pools.count "$LKE_NODE_COUNT" \
        --no-defaults \
        --json 2>&1) || true

    local lke_id
    lke_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "")

    if [ -z "$lke_id" ]; then
        fail "provision failed"
        info "$result"
        exit 1
    fi

    state_write lke_id "$lke_id"
    state_write lke_label "$label"
    state_write lke_region "$LKE_REGION"

    pass "provisioned: $label (id=$lke_id)"
    echo "  ──────────────────────────────────────────────"
}

lke_wait() {
    local lke_id
    lke_id=$(state_read lke_id)

    echo ""
    echo "  lke-wait"
    echo "  ──────────────────────────────────────────────"

    # LKE has no cluster-level status field — ready when kubeconfig works and nodes are up.

    # Fetch kubeconfig (may need retries — API key takes a moment to generate)
    info "fetching kubeconfig..."
    for i in $(seq 1 10); do
        if "$LINODE_CLI" lke kubeconfig-view "$lke_id" --json 2>/dev/null | \
            python3 -c "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)[0]['kubeconfig']).decode())" \
            > "$STATE/lke_kubeconfig" 2>/dev/null && [ -s "$STATE/lke_kubeconfig" ]; then
            pass "kubeconfig fetched"
            break
        fi
        info "kubeconfig not ready yet (attempt $i/10)..."
        sleep 15
    done

    export KUBECONFIG="$STATE/lke_kubeconfig"
    state_write lke_kubeconfig_path "$(cd "$STATE" && pwd)/lke_kubeconfig"

    # Wait for nodes
    info "waiting for nodes..."
    for i in $(seq 1 40); do
        local ready_nodes
        ready_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || true)
        if [ "$ready_nodes" -ge "$LKE_NODE_COUNT" ]; then
            pass "nodes ready: $ready_nodes/$LKE_NODE_COUNT"
            break
        fi
        if [ "$i" -eq 40 ]; then
            fail "nodes not ready after 10 minutes"
            kubectl get nodes 2>/dev/null || true
            exit 1
        fi
        info "nodes: $ready_nodes/$LKE_NODE_COUNT ready (attempt $i/40)..."
        sleep 15
    done

    # Get node external IPv4 for NodePort access
    local node_ip
    node_ip=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null | awk '{print $1}')
    if [ -z "$node_ip" ] || echo "$node_ip" | grep -q ':'; then
        # Fallback: filter for IPv4 only
        node_ip=$(kubectl get nodes -o jsonpath='{range .items[0].status.addresses[*]}{.type} {.address}{"\n"}{end}' 2>/dev/null | \
            grep ExternalIP | awk '{print $2}' | grep -v ':' | head -1)
    fi
    if [ -z "$node_ip" ]; then
        node_ip=$(kubectl get nodes -o jsonpath='{range .items[0].status.addresses[*]}{.type} {.address}{"\n"}{end}' 2>/dev/null | \
            grep InternalIP | awk '{print $2}' | grep -v ':' | head -1)
    fi

    if [ -n "$node_ip" ]; then
        state_write lke_node_ip "$node_ip"
        pass "node IP: $node_ip"
    else
        fail "could not determine node IP"
        exit 1
    fi

    echo "  ──────────────────────────────────────────────"
}

lke_destroy() {
    local lke_id
    lke_id=$(cat "$STATE/lke_id" 2>/dev/null || echo "")

    if [ -z "$lke_id" ]; then
        info "no LKE cluster to destroy"
        return 0
    fi

    local label
    label=$(cat "$STATE/lke_label" 2>/dev/null || echo "unknown")

    echo ""
    echo "  lke-destroy"
    echo "  ──────────────────────────────────────────────"
    info "destroying $label (id=$lke_id)..."

    "$LINODE_CLI" lke cluster-delete "$lke_id" 2>/dev/null || true
    rm -f "$STATE"/lke_*
    pass "destroyed $label"
    echo "  ──────────────────────────────────────────────"
}

# ─── Direct execution ─────────────────────────────

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-help}" in
        provision) lke_provision ;;
        wait)      lke_wait ;;
        destroy)   lke_destroy ;;
        *)         echo "usage: lke.sh {provision|wait|destroy}" >&2; exit 1 ;;
    esac
fi
