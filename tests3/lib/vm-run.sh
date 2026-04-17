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

# Translate host-absolute paths inside $TARGET to VM-absolute paths. The host repo
# lives at "$ROOT" (e.g. /home/dima/dev/vexa); on the VM it lives at /root/vexa.
# Every VAR=<path-under-root> in $TARGET gets rewritten. Example:
#   SCOPE=/home/dima/dev/vexa/tests3/releases/foo/scope.yaml
#     → SCOPE=/root/vexa/tests3/releases/foo/scope.yaml
TARGET_VM="${TARGET//$ROOT/\/root\/vexa}"
if [ "$TARGET" != "$TARGET_VM" ]; then
    info "translated paths: host $ROOT → VM /root/vexa"
fi

set +e
vm_ssh "cd /root/vexa && make -C tests3 $TARGET_VM DEPLOY_MODE=$VM_MODE"
EXIT=$?
set -e

# Pull JSON reports from the VM back to the host .state/reports/<mode>/ so the
# aggregator can include them in the release report (even if the test failed).
# Use tar-over-ssh rather than scp so glob expansion works reliably.
mkdir -p "$STATE/reports/$VM_MODE"
ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@$VM_IP" \
    "cd /root/vexa/tests3/.state/reports/$VM_MODE 2>/dev/null && tar cf - *.json 2>/dev/null" \
    | tar xf - -C "$STATE/reports/$VM_MODE/" 2>/dev/null || true

PULLED=$(find "$STATE/reports/$VM_MODE" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l)
info "pulled $PULLED report(s) from $VM_IP"

echo "  ──────────────────────────────────────────────"

exit $EXIT
