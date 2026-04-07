#!/usr/bin/env bash
# Deploy lite on a freshly provisioned VM.
# Reads: .state/vm_ip, .state/vm_branch
# Reads local: .env (for TRANSCRIPTION_SERVICE_URL + TOKEN)
set -euo pipefail
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/vm.sh"

BRANCH=$(state_read vm_branch)

echo ""
echo "  vm-setup-lite"
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
vm_ssh "apt-get update -qq && apt-get install -y -qq make git curl jq python3 python3-pip && pip3 install --break-system-packages websockets 2>/dev/null" 2>&1 | tail -1
pass "prereqs: make, git, curl, jq, python3"

info "installing docker..."
vm_ssh "curl -fsSL https://get.docker.com | sh" 2>&1 | tail -1
vm_ssh "docker --version"
pass "docker installed"

# ── 3. Clone repo ────────────────────────────────
info "cloning repo (branch=$BRANCH)..."
vm_ssh "git clone --branch $BRANCH $REPO_URL /root/vexa" 2>&1 | tail -1
pass "repo cloned at /root/vexa"

# ── 4. Start postgres (lite needs external pg) ───
info "starting postgres..."
vm_ssh "docker run -d --name pg --network host \
    -e POSTGRES_DB=vexa \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    postgres:17-alpine" 2>&1 | tail -1

# Wait for postgres ready
for i in $(seq 1 12); do
    if vm_ssh "docker exec pg pg_isready -U postgres -d vexa -q" 2>/dev/null; then
        break
    fi
    sleep 5
done
pass "postgres ready"

# ── 5. Pull lite image ───────────────────────────
info "pulling lite image (vexaai/vexa-lite:dev)..."
vm_ssh "docker pull vexaai/vexa-lite:dev" 2>&1 | tail -3
pass "lite image pulled"

# ── 6. Start lite container ──────────────────────
info "starting lite container..."
vm_ssh "docker run -d --name vexa --shm-size=2g --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e DATABASE_URL=postgresql://postgres:postgres@localhost:5432/vexa \
    -e DB_PASSWORD=postgres \
    -e DB_SSL_MODE=disable \
    -e REDIS_URL=redis://localhost:6379/0 \
    -e ADMIN_API_TOKEN=changeme \
    -e TRANSCRIBER_URL=$TX_URL \
    -e TRANSCRIBER_API_KEY=$TX_TOKEN \
    vexaai/vexa-lite:dev" 2>&1 | tail -1

# ── 7. Wait for services ─────────────────────────
info "waiting for services (up to 2 min)..."
for i in $(seq 1 24); do
    if vm_ssh "curl -sf -o /dev/null http://localhost:8056/" 2>/dev/null; then
        break
    fi
    sleep 5
done

VM_IP=$(state_read vm_ip)
for ep in "8056:gateway" "8057:admin-api" "3000:dashboard"; do
    PORT=${ep%%:*}
    NAME=${ep##*:}
    CODE=$(vm_ssh "curl -sf -o /dev/null -w '%{http_code}' http://localhost:$PORT/ 2>/dev/null || echo 000")
    if [ "$CODE" = "200" ]; then
        pass "$NAME: http://$VM_IP:$PORT"
    else
        fail "$NAME: HTTP $CODE"
    fi
done

# ── 8. Init DB + create user ─────────────────────
info "initializing database..."
vm_ssh "docker exec -e DB_PASSWORD=postgres -e DB_SSL_MODE=disable vexa python3 -c 'import asyncio; from admin_models.database import init_db; asyncio.run(init_db())'" 2>&1 | tail -1
vm_ssh "docker exec -e DB_PASSWORD=postgres -e DB_SSL_MODE=disable vexa python3 -c 'import asyncio; from meeting_api.database import init_db; asyncio.run(init_db())'" 2>&1 | tail -1
pass "database initialized"

state_write vm_setup complete

echo "  ──────────────────────────────────────────────"
echo ""
