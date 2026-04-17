#!/usr/bin/env bash
# Run a tests3 Makefile target on the VM via SSH.
# The same tests3 code in the cloned repo runs on the VM.
#
# Before running: syncs .state/image_tag host → VM (so test reports carry the tag).
# After running: pulls .state/reports/<mode>/ VM → host (so the aggregator sees them).
#
# Usage: lib/vm-run.sh <target>
set -euo pipefail
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/vm.sh"

TARGET=${1:?usage: vm-run.sh <target>}
VM_MODE=$(state_read vm_mode)
VM_IP=$(state_read vm_ip)

echo ""
echo "  vm-run: $TARGET (mode=$VM_MODE)"
echo "  ──────────────────────────────────────────────"

# Push image_tag to the VM so test reports include it.
if [ -f "$STATE/image_tag" ]; then
    scp -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$STATE/image_tag" "root@$VM_IP:/root/vexa/tests3/.state/image_tag" 2>/dev/null || true
fi

set +e
vm_ssh "cd /root/vexa && make -C tests3 $TARGET DEPLOY_MODE=$VM_MODE"
EXIT=$?
set -e

# Pull JSON reports from the VM back to the host .state/reports/<mode>/ so the
# aggregator can include them in the release report (even if the test failed).
mkdir -p "$STATE/reports/$VM_MODE"
scp -q -r -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "root@$VM_IP:/root/vexa/tests3/.state/reports/$VM_MODE/." \
    "$STATE/reports/$VM_MODE/" 2>/dev/null || true

echo "  ──────────────────────────────────────────────"

exit $EXIT
