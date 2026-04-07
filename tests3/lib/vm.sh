#!/usr/bin/env bash
# VM lifecycle: provision, wait, ssh, scp, destroy.
# Source this for functions, or call directly: lib/vm.sh provision compose
set -euo pipefail
source "$(dirname "$0")/common.sh"

LINODE_CLI="${LINODE_CLI:-/home/dima/anaconda3/bin/linode-cli}"
VM_TYPE="${VM_TYPE:-g6-standard-6}"
VM_IMAGE="${VM_IMAGE:-linode/ubuntu24.04}"
VM_REGION="${VM_REGION:-us-ord}"
REPO_URL="${REPO_URL:-https://github.com/Vexa-ai/vexa.git}"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa.pub}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

vm_check_prereqs() {
    if [ ! -f "$SSH_KEY" ]; then
        fail "SSH key not found: $SSH_KEY"
        exit 1
    fi
    if ! command -v "$LINODE_CLI" &>/dev/null; then
        fail "linode-cli not found at $LINODE_CLI"
        info "install: pip install linode-cli && linode-cli configure"
        exit 1
    fi
}

vm_provision() {
    local mode="${1:-compose}"

    # Don't double-provision
    if state_exists vm_id; then
        local existing_ip
        existing_ip=$(state_read vm_ip)
        info "VM already exists at $existing_ip ($(state_read vm_mode))"
        info "run 'make -C tests3 vm-destroy' first, or reuse with vm-smoke"
        return 0
    fi

    vm_check_prereqs

    local label="vexa-t3-${mode}-$(date +%H%M)"

    echo ""
    echo "  vm-provision"
    echo "  ──────────────────────────────────────────────"
    info "creating $label ($VM_TYPE, $VM_REGION, $VM_IMAGE)..."

    local result
    result=$("$LINODE_CLI" linodes create \
        --type "$VM_TYPE" \
        --image "$VM_IMAGE" \
        --region "$VM_REGION" \
        --label "$label" \
        --root_pass "$(openssl rand -base64 32)" \
        --authorized_keys "$(cat "$SSH_KEY")" \
        --json 2>/dev/null)

    local vm_id vm_ip
    vm_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
    vm_ip=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ipv4'][0])")

    if [ -z "$vm_id" ] || [ -z "$vm_ip" ]; then
        fail "provision failed"
        info "$result"
        exit 1
    fi

    state_write vm_id "$vm_id"
    state_write vm_ip "$vm_ip"
    state_write vm_mode "$mode"
    state_write vm_label "$label"
    state_write vm_branch "$BRANCH"

    pass "provisioned: $label (id=$vm_id ip=$vm_ip)"
    echo "  ──────────────────────────────────────────────"
}

vm_wait_ssh() {
    local ip
    ip=$(state_read vm_ip)

    info "waiting for SSH on $ip..."
    for i in $(seq 1 30); do
        if ssh $SSH_OPTS "root@$ip" "echo ok" &>/dev/null; then
            pass "SSH ready"
            return 0
        fi
        sleep 10
    done
    fail "SSH not ready after 5 minutes"
    exit 1
}

vm_ssh() {
    local ip
    ip=$(state_read vm_ip)
    ssh $SSH_OPTS "root@$ip" "$@"
}

vm_scp() {
    local ip
    ip=$(state_read vm_ip)
    scp $SSH_OPTS "$1" "root@$ip:$2"
}

vm_destroy() {
    local vm_id
    vm_id=$(cat "$STATE/vm_id" 2>/dev/null || echo "")

    if [ -z "$vm_id" ]; then
        info "no VM to destroy"
        return 0
    fi

    local label
    label=$(cat "$STATE/vm_label" 2>/dev/null || echo "unknown")
    local ip
    ip=$(cat "$STATE/vm_ip" 2>/dev/null || echo "?")

    echo ""
    echo "  vm-destroy"
    echo "  ──────────────────────────────────────────────"
    info "destroying $label ($ip, id=$vm_id)..."

    "$LINODE_CLI" linodes delete "$vm_id" 2>/dev/null || true
    rm -f "$STATE"/vm_*
    pass "destroyed $label"
    echo "  ──────────────────────────────────────────────"
}

# ─── Direct execution ─────────────────────────────

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-help}" in
        provision) vm_provision "${2:-compose}" ;;
        wait)      vm_wait_ssh ;;
        destroy)   vm_destroy ;;
        ssh)       shift; vm_ssh "$@" ;;
        *)         echo "usage: vm.sh {provision|wait|destroy|ssh} [args]" >&2; exit 1 ;;
    esac
fi
