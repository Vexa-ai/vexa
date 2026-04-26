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
# Branch the VM was provisioned with. Reset scripts use this to refresh the
# checkout on the VM (was previously hardcoded to `dev`, which broke whenever
# the cycle ran on a release/<id> branch and `dev` didn't exist on origin).
VM_BRANCH=$(state_read vm_branch)
echo ""
echo "  vm-reset: $(basename "$SCRIPT") on $VM_IP (branch=${VM_BRANCH})"
echo "  ──────────────────────────────────────────────"

# Inject VM_BRANCH into the remote shell. `ssh -o SendEnv` would require
# sshd config; the inline-export form here is portable and matches how the
# rest of the harness wraps SSH.
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "root@$VM_IP" "VM_BRANCH=${VM_BRANCH} bash -s" < "$SCRIPT"

echo "  ──────────────────────────────────────────────"
