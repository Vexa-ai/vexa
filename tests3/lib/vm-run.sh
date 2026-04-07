#!/usr/bin/env bash
# Run a tests3 Makefile target on the VM via SSH.
# The same tests3 code in the cloned repo runs on the VM.
# Usage: lib/vm-run.sh <target>
set -euo pipefail
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/vm.sh"

TARGET=${1:?usage: vm-run.sh <target>}
VM_MODE=$(state_read vm_mode)

echo ""
echo "  vm-run: $TARGET (mode=$VM_MODE)"
echo "  ──────────────────────────────────────────────"

vm_ssh "cd /root/vexa && make -C tests3 $TARGET DEPLOY_MODE=$VM_MODE"
EXIT=$?

echo "  ──────────────────────────────────────────────"

exit $EXIT
