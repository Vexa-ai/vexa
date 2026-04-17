#!/usr/bin/env bash
# Run a reset script on a VM via SSH (stdin-piped).
# Used by `make vm-reset-<mode>` targets.
#
# Usage: tests3/lib/vm-reset.sh <path-to-reset-script>
set -euo pipefail
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/vm.sh"

SCRIPT="${1:?usage: vm-reset.sh <reset-script>}"
[ -f "$SCRIPT" ] || { echo "no such script: $SCRIPT" >&2; exit 2; }

VM_IP=$(state_read vm_ip)
echo ""
echo "  vm-reset: $(basename "$SCRIPT") on $VM_IP"
echo "  ──────────────────────────────────────────────"

ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "root@$VM_IP" "bash -s" < "$SCRIPT"

echo "  ──────────────────────────────────────────────"
