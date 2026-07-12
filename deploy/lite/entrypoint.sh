#!/bin/bash
# =============================================================================
# Vexa Lite (v0.12) — container entrypoint
# =============================================================================
# 1. Normalizes the runtime env (every var supervisord references via %(ENV_X)s MUST exist,
#    or supervisord refuses to start that program — so we default them all here).
# 2. Derives DATABASE_URL + REDIS_URL from parts (or parses a supplied URL into parts).
# 3. Waits for the (external) PostgreSQL — schema convergence runs in-process on each
#    service's startup (admin-api/meeting-api ensure_schema()).
# 4. Hands off to supervisord, which brings up the whole control plane.
# =============================================================================
set -e

echo "=============================================="
echo "  Vexa Lite (v0.12) — starting container"
echo "=============================================="

# ─── Redis (internal by default; an external REDIS_URL is honored) ────────────────────────────────
if [ -z "${REDIS_URL:-}" ]; then
    export REDIS_HOST="${REDIS_HOST:-localhost}"
    export REDIS_PORT="${REDIS_PORT:-6379}"
    export REDIS_URL="redis://${REDIS_HOST}:${REDIS_PORT}/0"
fi

# ─── Database — DB_* only. Each service builds its own async URL (postgresql+asyncpg://) from these
#     (admin_api/_database_url, meeting_api/_database_url). We deliberately do NOT export DATABASE_URL:
#     a plain `postgresql://` would force SQLAlchemy onto the psycopg2 (sync) driver, which lite does
#     not install (asyncpg only). For an external managed DB, set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD.
export DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${DB_PORT:-5432}"
export DB_NAME="${DB_NAME:-vexa}"
export DB_USER="${DB_USER:-postgres}"
export DB_PASSWORD="${DB_PASSWORD:-postgres}"

# ─── Defaults for every var supervisord interpolates (empty is fine; must be SET) ─────────────────
export LOG_LEVEL="${LOG_LEVEL:-info}"
export DISPLAY="${DISPLAY:-:99}"
export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-${ADMIN_TOKEN:-changeme}}"
export INTERNAL_API_SECRET="${INTERNAL_API_SECRET:-lite-internal-secret}"

export TRANSCRIPTION_SERVICE_URL="${TRANSCRIPTION_SERVICE_URL:-}"
export TRANSCRIPTION_SERVICE_TOKEN="${TRANSCRIPTION_SERVICE_TOKEN:-}"

export MINIO_ENDPOINT="${MINIO_ENDPOINT:-}"
export MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-}"
export MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-}"
export MINIO_BUCKET="${MINIO_BUCKET:-vexa}"
export MINIO_SECURE="${MINIO_SECURE:-false}"

# Process-backend launchers — DEFAULTS ONLY: an operator-provided BOT_COMMAND /
# AGENT_WORKER_COMMAND on the container env wins. supervisord interpolates these into the
# runtime program via %(ENV_…)s — never hardcode them there (that clobbers operator env).
export BOT_COMMAND="${BOT_COMMAND:-/usr/local/bin/vexa-bot-launch}"
export AGENT_WORKER_COMMAND="${AGENT_WORKER_COMMAND:-/usr/local/bin/vexa-agent-worker}"

# Agent control plane + worker (BYO inference; credentials brokered by the runtime).
export VEXA_AGENT_DEFAULT_SUBJECT="${VEXA_AGENT_DEFAULT_SUBJECT:-u_live}"
export VEXA_DISPATCH_SIGNING_KEY="${VEXA_DISPATCH_SIGNING_KEY:-dev-dispatch-signing-key}"
export VEXA_BOT_API_KEY="${VEXA_BOT_API_KEY:-}"
export VEXA_AGENT_MODEL="${VEXA_AGENT_MODEL:-}"
export VEXA_MEETING_MODEL="${VEXA_MEETING_MODEL:-}"
# HOST_CLAUDE_CREDENTIALS (config.v1 `model_inference`): path of a claude credentials JSON as seen
# INSIDE this lite container (mount it in, e.g. -v ~/.claude/.credentials.json:/claude-creds.json:ro
# and set HOST_CLAUDE_CREDENTIALS=/claude-creds.json). Lite's runtime uses the process backend, so
# the worker reads the file directly; the runtime's config.v1 file probe verifies it on /health.
# Alternative: leave empty and set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN instead.
export HOST_CLAUDE_CREDENTIALS="${HOST_CLAUDE_CREDENTIALS:-}"
# Process backend: the worker's claude CLI reads ~/.claude/.credentials.json in THIS container —
# link it to the mounted file so a subscription login actually reaches inference (the mount may be
# a directory whose file appears after a host `claude /login`; a dangling symlink resolves then).
if [ -n "$HOST_CLAUDE_CREDENTIALS" ] && [ ! -e /root/.claude/.credentials.json ]; then
    mkdir -p /root/.claude
    ln -sf "$HOST_CLAUDE_CREDENTIALS" /root/.claude/.credentials.json
fi
export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-}"
export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-}"
export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-}"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-}"

# Dashboard + terminal (both Next.js UIs)
export VEXA_PUBLIC_API_URL="${VEXA_PUBLIC_API_URL:-http://localhost:8056}"
export VEXA_API_KEY="${VEXA_API_KEY:-}"
export TERMINAL_PUBLIC_URL="${TERMINAL_PUBLIC_URL:-http://localhost:3001}"
export NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-vexa-lite-nextauth-secret}"
export JWT_SECRET="${JWT_SECRET:-vexa-lite-jwt-secret}"

# Workspace store for the agent (shared dir; the worker runs in-process, no volume bind).
mkdir -p /workspaces /var/lib/redis /var/run/redis
chmod 777 /workspaces 2>/dev/null || true

echo "Configuration:"
echo "  - Redis URL:        ${REDIS_URL}"
echo "  - Database:         postgresql+asyncpg://${DB_USER}:***@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "  - Transcription:    ${TRANSCRIPTION_SERVICE_URL:-NOT SET (bots capture, no transcript)}"
echo "  - Object storage:   ${MINIO_ENDPOINT:-NOT SET (recordings disabled)}"
echo "  - Log level:        ${LOG_LEVEL}"
echo ""

# ─── Wait for PostgreSQL (external) ───────────────────────────────────────────────────────────────
if [ -n "$DB_HOST" ]; then
    echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
    for attempt in $(seq 1 30); do
        if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q 2>/dev/null; then
            echo "PostgreSQL is ready."
            break
        fi
        [ "$attempt" -eq 30 ] && echo "WARNING: PostgreSQL not reachable after 30 attempts; starting anyway."
        sleep 2
    done
    echo ""
fi

# Background: once admin-api is up, mint a self-host API key and hand it to the UIs (zero-login).
# No-op if VEXA_API_KEY was supplied. Only meaningful for the supervisord CMD (the real bring-up).
case "$*" in
    *supervisord*) /usr/local/bin/provision-key.sh & ;;
esac

echo "Starting services via supervisord..."
exec "$@"
