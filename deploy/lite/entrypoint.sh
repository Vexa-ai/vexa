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
# The internal valkey's default user requires a password, minted per boot unless given. The services
# connect with it; a bot or worker never does — each connects as a Redis user of its own.
export REDIS_PASSWORD="${REDIS_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}"
if [ -z "${REDIS_URL:-}" ]; then
    export REDIS_HOST="${REDIS_HOST:-localhost}"
    export REDIS_PORT="${REDIS_PORT:-6379}"
    export REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/0"
fi

# ─── Database — DB_* only. Each service builds its own async URL (postgresql+asyncpg://) from these
#     (admin_api/_database_url, meeting_api/_database_url). We deliberately do NOT export DATABASE_URL:
#     a plain `postgresql://` would force SQLAlchemy onto the psycopg2 (sync) driver, which lite does
#     not install (asyncpg only). For an external managed DB, set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD.
export DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${DB_PORT:-5432}"
export DB_NAME="${DB_NAME:-vexa}"
export DB_USER="${DB_USER:-postgres}"
# The database password has no default: the old one, `postgres`, is published in this repository.
# `make -C deploy/lite up` mints one into the repo-root .env and sets it on its postgres sidecar; with
# your own database, pass that database's password. Refused like compose's postgres refuses it.
case "${DB_PASSWORD:-}" in
    ""|postgres|password|vexa-internal-secret|lite-internal-secret|changeme|change-me|CHANGE-ME|default|secret)
        echo "ERROR: DB_PASSWORD is unset or a value published in the Vexa repository - refusing to start." >&2
        echo "  make -C deploy/lite up mints one; with your own database, pass -e DB_PASSWORD=<its password>." >&2
        exit 1;;
esac
export DB_PASSWORD="${DB_PASSWORD}"

# ─── Defaults for every var supervisord interpolates (empty is fine; must be SET) ─────────────────
export LOG_LEVEL="${LOG_LEVEL:-info}"
export DISPLAY="${DISPLAY:-:99}"
# Who may sign in to the terminal besides existing users and admins — exact addresses and @domain
# entries, comma-separated (admin-api reads it; see deploy/compose/.env.example). Empty = nobody new
# once an admin exists. Pass it with `docker run -e VEXA_SIGNIN_ALLOW=@example.com …`.
export VEXA_SIGNIN_ALLOW="${VEXA_SIGNIN_ALLOW:-}"
# The administrators, comma-separated full addresses (admin-api reads it; the terminal only asks).
# Naming them closes the admin claim — set it whenever the terminal is reachable from outside.
export VEXA_ADMIN_EMAILS="${VEXA_ADMIN_EMAILS:-}"
# The mail relay the terminal sends the emailed sign-in link through — the deployment's
# VEXA_MAIL_SMTP_* family (see deploy/compose/.env.example). Empty host: no link is delivered.
export VEXA_MAIL_SMTP_HOST="${VEXA_MAIL_SMTP_HOST:-}"
export VEXA_MAIL_SMTP_PORT="${VEXA_MAIL_SMTP_PORT:-}"
export VEXA_MAIL_SMTP_FROM="${VEXA_MAIL_SMTP_FROM:-}"
export VEXA_MAIL_SMTP_USER="${VEXA_MAIL_SMTP_USER:-}"
export VEXA_MAIL_SMTP_PASSWORD="${VEXA_MAIL_SMTP_PASSWORD:-}"
export VEXA_MAIL_SMTP_SECURE="${VEXA_MAIL_SMTP_SECURE:-}"
export VEXA_MAIL_SMTP_TLS_INSECURE="${VEXA_MAIL_SMTP_TLS_INSECURE:-}"
# A chat turn continues past its tool-call budget into a fresh window, at most this many times
# (agent-api validates both and stamps them into every worker; see deploy/compose/.env.example).
export VEXA_AGENT_AUTO_CONTINUE_CHAT="${VEXA_AGENT_AUTO_CONTINUE_CHAT:-1}"
export VEXA_AGENT_MAX_CHAT_CONTINUATIONS="${VEXA_AGENT_MAX_CHAT_CONTINUATIONS:-4}"
# The admin tier, on the same terms as the internal tier below and for a LARGER blast radius:
# this token mints an API key for ANY user and HS256-signs every per-spawn MeetingToken. It
# defaulted to the published literal `changeme`, so every lite stack nobody configured shared one
# admin secret that is in this repository (A19). lite is ONE container, so the fallback can be
# MINTED per boot: admin-api, meeting-api, the dashboard, the terminal and provision-key all read
# it from this same environment. Set ADMIN_API_TOKEN (or ADMIN_TOKEN) explicitly when something
# outside the container has to present it.
export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-${ADMIN_TOKEN:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}}"
# The internal tier. lite is ONE container, so every service that shares this secret shares this
# process's environment — which means the fallback can be MINTED per boot instead of shipped as
# a literal. `lite-internal-secret` was published in this repository and was the exact value
# agent-api's _internal_caller compared against, so anyone who could reach the port was the
# internal tier (F95). A random per-boot value keeps the one-command quickstart working and is
# nobody's to guess; set INTERNAL_API_SECRET explicitly when something outside talks in.
export INTERNAL_API_SECRET="${INTERNAL_API_SECRET:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}"
# The runtime caller credential: the runtime refuses every workload/schedule call without it. It is
# NOT exported — every supervisord program inherits supervisord's environment — but rendered into the
# environment= of the runtime, agent-api and meeting-api only (bin/render-supervisord, below). Minted
# per boot like the internal tier; pass RUNTIME_API_TOKEN to fix it.
runtime_api_token="${RUNTIME_API_TOKEN:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}"
unset RUNTIME_API_TOKEN
# The worker toolbelt: agent-api signs each worker's delegation token, admin-api verifies it.
export VEXA_MCP_DELEGATION_SECRET="${VEXA_MCP_DELEGATION_SECRET:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}"
export DEFAULT_BOT_NAME="${DEFAULT_BOT_NAME:-Vexa}"

# Optional Google Meet speaker-stream tuning. Empty values preserve bot defaults; the runtime
# profile forwards configured values to every spawned bot process.
export BOT_ALONE_SILENCE_WINDOW_MS="${BOT_ALONE_SILENCE_WINDOW_MS:-}"
export BOT_SPEAKER_MIN_AUDIO_SEC="${BOT_SPEAKER_MIN_AUDIO_SEC:-}"
export BOT_SPEAKER_SUBMIT_INTERVAL_SEC="${BOT_SPEAKER_SUBMIT_INTERVAL_SEC:-}"
export BOT_SPEAKER_CONFIRM_THRESHOLD="${BOT_SPEAKER_CONFIRM_THRESHOLD:-}"
export BOT_SPEAKER_MAX_BUFFER_SEC="${BOT_SPEAKER_MAX_BUFFER_SEC:-}"
export BOT_SPEAKER_IDLE_TIMEOUT_SEC="${BOT_SPEAKER_IDLE_TIMEOUT_SEC:-}"

export TRANSCRIPTION_SERVICE_URL="${TRANSCRIPTION_SERVICE_URL:-}"
export TRANSCRIPTION_SERVICE_TOKEN="${TRANSCRIPTION_SERVICE_TOKEN:-}"
# STT model id for validating backends (Groq/vLLM); empty → whisper-1.
export TRANSCRIPTION_MODEL="${TRANSCRIPTION_MODEL:-}"

# Optional operator-owned service-authority.v1 boundary. The config stays credential-free; the
# signing secret remains a separate inherited environment value and is never printed below.
export VEXA_SERVICE_AUTHORITY_CONFIG="${VEXA_SERVICE_AUTHORITY_CONFIG:-}"
export VEXA_SERVICE_AUTHORITY_SECRET="${VEXA_SERVICE_AUTHORITY_SECRET:-}"
export VEXA_SYSTEM_WEBHOOK_URL="${VEXA_SYSTEM_WEBHOOK_URL:-}"
export VEXA_SYSTEM_WEBHOOK_SECRET="${VEXA_SYSTEM_WEBHOOK_SECRET:-}"
export VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP="${VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP:-false}"
export VEXA_SYSTEM_WEBHOOK_TIMEOUT_S="${VEXA_SYSTEM_WEBHOOK_TIMEOUT_S:-10}"

export MINIO_ENDPOINT="${MINIO_ENDPOINT:-}"
export MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-}"
export MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-}"
export MINIO_BUCKET="${MINIO_BUCKET:-vexa}"
export MINIO_SECURE="${MINIO_SECURE:-false}"

# Gateway edge guard (fastapi-guard): ON by default with generous limits (owner ruling).
# Opt out with -e GUARD_ENABLED=false on the container. Other GUARD_* tuning keys
# (GUARD_RATE_LIMIT_RPM, GUARD_TRUSTED_PROXIES, …) flow through container env untouched.
export GUARD_ENABLED="${GUARD_ENABLED:-true}"
export GUARD_WS_ENABLED="${GUARD_WS_ENABLED:-false}"

# Process-backend launchers — DEFAULTS ONLY: an operator-provided BOT_COMMAND /
# AGENT_WORKER_COMMAND on the container env wins. supervisord interpolates these into the
# runtime program via %(ENV_…)s — never hardcode them there (that clobbers operator env).
export BOT_COMMAND="${BOT_COMMAND:-/usr/local/bin/vexa-bot-launch}"
export AGENT_WORKER_COMMAND="${AGENT_WORKER_COMMAND:-/usr/local/bin/vexa-agent-worker}"

# Agent control plane + worker (BYO inference; credentials brokered by the runtime).
# (VEXA_DISPATCH_SIGNING_KEY is set below, once the state directory is known.)
export VEXA_BOT_API_KEY="${VEXA_BOT_API_KEY:-}"
export VEXA_AGENT_MODEL="${VEXA_AGENT_MODEL:-}"
# HOST_CLAUDE_CREDENTIALS (config.v1 `model_inference`): path of a claude credentials JSON as seen
# INSIDE this lite container. Mount the DIRECTORY, not the file — `make up` does
#   -v ~/.claude:/var/lib/vexa/host-claude:ro
#   -e HOST_CLAUDE_CREDENTIALS=/var/lib/vexa/host-claude/.credentials.json
# because a single-FILE bind is pinned to the inode it was created with, and the claude CLI
# refreshes an expiring token by rename(2)-ing a NEW inode over .credentials.json: a long-lived
# container then serves the pre-refresh token until it is restarted. Lite's runtime uses the
# process backend, so the worker reads the file directly; the runtime's config.v1 file probe
# verifies it on /health.
# Alternative: leave empty and set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN instead.
export HOST_CLAUDE_CREDENTIALS="${HOST_CLAUDE_CREDENTIALS:-}"
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
# The terminal's signing secret — it signs sign-in cookies and (through a derived key) every emailed
# sign-in link, so it has NO published default, and the terminal refuses to start on one shorter
# than 32 bytes or published in this repository. Unset, it is minted on first boot and kept in
# $VEXA_LITE_STATE_DIR (default /var/lib/vexa/state), so a restart of this container keeps sessions
# and links valid. Mount a volume there, or pass NEXTAUTH_SECRET (`openssl rand -hex 32`), to keep it
# across re-creating the container.
lite_state_dir="${VEXA_LITE_STATE_DIR:-/var/lib/vexa/state}"
export VEXA_LITE_STATE_DIR="$lite_state_dir"
# gateway-identity.v1 — the gateway signs the identity it resolved with an Ed25519 PRIVATE key;
# agent-api and meeting-api verify with the PUBLIC key and cannot sign. The pair is generated on the
# first boot into $VEXA_LITE_STATE_DIR/identity (0700, the private key 0600) and reused on every
# later one, so a restart keeps it; mount a volume there to keep it across re-creating the
# container. supervisord names the signing key to [program:gateway] alone and the public key to
# agent-api and meeting-api. Every Lite program is a root process in one container, so the file
# mode, not a mount, is what keeps the agent workers (non-root) away from it. Delete
# identity/signing-key.pem and restart to rotate. Prints which file it wrote, never a key.
mkdir -p -m 0700 "$lite_state_dir/identity"
/opt/venvs/gateway/bin/python /app/gateway/src/gateway/identity_token.py keygen \
    "$lite_state_dir/identity/signing-key.pem" "$lite_state_dir/identity/public-key.pem"
export NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-$(/usr/local/bin/persisted-secret "$lite_state_dir/nextauth-secret")}"
# The key agent-api signs each dispatch's identity token with. Its old default was published in this
# repository and agent-api refuses to boot on it. Unset, it is minted on the first boot and kept in
# $VEXA_LITE_STATE_DIR like the terminal's secret. A .env seeded from an older compose .env may still
# carry the published value; that one is set aside for the kept key, with a warning.
if [ "${VEXA_DISPATCH_SIGNING_KEY:-}" = "dev-dispatch-signing-key" ]; then
    echo "WARNING: VEXA_DISPATCH_SIGNING_KEY holds the value published in the Vexa repository; using the key kept in $lite_state_dir instead." >&2
    unset VEXA_DISPATCH_SIGNING_KEY
fi
export VEXA_DISPATCH_SIGNING_KEY="${VEXA_DISPATCH_SIGNING_KEY:-$(/usr/local/bin/persisted-secret "$lite_state_dir/dispatch-signing-key")}"
# Read by no Lite program; minted per boot like the internal tier so no published value is exported.
export JWT_SECRET="${JWT_SECRET:-$(python3 -c "import secrets; print(secrets.token_hex(32))")}"

# Workspace store for the agent (shared dir; the worker runs in-process, no volume bind).
mkdir -p /workspaces /var/lib/redis /var/run/redis
chmod 777 /workspaces 2>/dev/null || true

echo "Configuration:"
echo "  - Redis URL:        $(printf '%s' "$REDIS_URL" | sed -E 's#//[^@/]*@#//***@#')"
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

# The supervisor config supervisord runs: the shipped template with the runtime caller credential in
# place, root-only. Refuses (and the container stops) on a credential that is short or not URL-safe.
case "$*" in
    *supervisord*)
        printf '%s' "$runtime_api_token" \
            | python3 /usr/local/bin/render-supervisord /etc/supervisor/conf.d/vexa.conf /run/vexa/supervisord.conf \
            || exit 1 ;;
esac
unset runtime_api_token

echo "Starting services via supervisord..."
exec "$@"
