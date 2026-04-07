#!/usr/bin/env bash
# Deploy compose on a freshly provisioned VM.
# Reads: .state/vm_ip, .state/vm_branch
# Reads local: .env (for TRANSCRIPTION_SERVICE_URL + TOKEN)
set -euo pipefail
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/vm.sh"

BRANCH=$(state_read vm_branch)

echo ""
echo "  vm-setup-compose"
echo "  ──────────────────────────────────────────────"

# ── 1. Read transcription creds from local .env ──
TX_URL=$(grep -E '^TRANSCRIPTION_SERVICE_URL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)
TX_TOKEN=$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)

if [ -z "$TX_URL" ]; then
    fail "TRANSCRIPTION_SERVICE_URL not set in local .env"
    exit 1
fi
pass "local creds: TX_URL=${TX_URL:0:40}..."

# ── 2. Install prereqs ───────────────────────────
info "installing prereqs..."
vm_ssh "apt-get update -qq && apt-get install -y -qq make git curl jq python3" 2>&1 | tail -1
pass "prereqs: make, git, curl, jq, python3"

info "installing docker..."
vm_ssh "curl -fsSL https://get.docker.com | sh" 2>&1 | tail -1
vm_ssh "docker --version"
pass "docker installed"

# ── 3. Clone repo ────────────────────────────────
info "cloning repo (branch=$BRANCH)..."
vm_ssh "git clone --branch $BRANCH $REPO_URL /root/vexa" 2>&1 | tail -1
pass "repo cloned at /root/vexa"

# ── 4. Configure .env ────────────────────────────
info "configuring .env..."
vm_ssh "cd /root/vexa/deploy/compose && make env" 2>&1 | tail -1

# Inject transcription creds
vm_ssh "cd /root/vexa && \
    sed -i 's|^#*TRANSCRIPTION_SERVICE_URL=.*|TRANSCRIPTION_SERVICE_URL=$TX_URL|' .env && \
    sed -i 's|^#*TRANSCRIPTION_SERVICE_TOKEN=.*|TRANSCRIPTION_SERVICE_TOKEN=$TX_TOKEN|' .env"
pass ".env configured with transcription creds"

# ── 5. Copy tests3 to VM (not in git yet) ─────────
info "syncing tests3 to VM..."
VM_IP=$(state_read vm_ip)
scp -r -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$ROOT/tests3" "root@$VM_IP:/root/vexa/" 2>/dev/null
pass "tests3 synced to VM"

# ── 6. Deploy ─────────────────────────────────────
info "running make all (this pulls images — may take 3-5 minutes)..."
DEPLOY_OK=false
for attempt in 1 2 3; do
    if vm_ssh "cd /root/vexa/deploy/compose && make all 2>&1 | tail -5"; then
        DEPLOY_OK=true
        break
    fi
    if [ "$attempt" -lt 3 ]; then
        info "attempt $attempt failed (docker pull may have timed out), retrying..."
        sleep 10
    fi
done

if [ "$DEPLOY_OK" = true ]; then
    pass "make all succeeded"
else
    fail "make all failed after 3 attempts"
    info "SSH in to debug: make -C tests3 vm-ssh"
    exit 1
fi

# ── 6. Verify ─────────────────────────────────────
VM_IP=$(state_read vm_ip)
info "verifying services..."
for ep in "8056:gateway" "8057:admin-api" "3001:dashboard"; do
    PORT=${ep%%:*}
    NAME=${ep##*:}
    CODE=$(vm_ssh "curl -sf -o /dev/null -w '%{http_code}' http://localhost:$PORT/ 2>/dev/null || echo 000")
    if [ "$CODE" = "200" ]; then
        pass "$NAME: http://$VM_IP:$PORT"
    else
        fail "$NAME: HTTP $CODE"
    fi
done

state_write vm_setup complete

echo "  ──────────────────────────────────────────────"
echo ""
