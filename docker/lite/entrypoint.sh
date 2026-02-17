#!/bin/bash
# =============================================================================
# Vexa Lite - Container Entrypoint
# =============================================================================
# This script initializes the Vexa Lite container:
# 1. Waits for external services (PostgreSQL, Redis)
# 2. Runs database migrations
# 3. Starts all services via supervisord
# =============================================================================

set -e

echo "=============================================="
echo "  Vexa Lite - Starting Container"
echo "=============================================="
echo ""

# -----------------------------------------------------------------------------
# Environment Setup
# -----------------------------------------------------------------------------

# Set defaults for environment variables

# Redis configuration - supports REDIS_URL or individual vars
# If REDIS_HOST is localhost or not set, use internal Redis server
USE_INTERNAL_REDIS=false
if [ -z "$REDIS_HOST" ] || [ "$REDIS_HOST" = "localhost" ] || [ "$REDIS_HOST" = "127.0.0.1" ]; then
    USE_INTERNAL_REDIS=true
    export REDIS_HOST="localhost"
    export REDIS_PORT="${REDIS_PORT:-6379}"
    export REDIS_USER=""
    export REDIS_PASSWORD=""
    export REDIS_URL="redis://localhost:${REDIS_PORT}/0"
elif [ -n "$REDIS_URL" ]; then
    # Parse REDIS_URL into individual vars
    # Format: redis://[user:password@]host:port[/db]
    REDIS_URL_NO_SCHEME="${REDIS_URL#*://}"
    # Check if there's auth (contains @)
    if [[ "$REDIS_URL_NO_SCHEME" == *"@"* ]]; then
        REDIS_AUTH="${REDIS_URL_NO_SCHEME%%@*}"
        REDIS_HOSTPORTDB="${REDIS_URL_NO_SCHEME#*@}"
        # Parse user and password
        if [[ "$REDIS_AUTH" == *":"* ]]; then
            export REDIS_USER="${REDIS_AUTH%%:*}"
            export REDIS_PASSWORD="${REDIS_AUTH#*:}"
        else
            export REDIS_USER="$REDIS_AUTH"
            export REDIS_PASSWORD=""
        fi
    else
        REDIS_HOSTPORTDB="$REDIS_URL_NO_SCHEME"
        export REDIS_USER=""
        export REDIS_PASSWORD=""
    fi
    # Parse host:port
    REDIS_HOSTPORT="${REDIS_HOSTPORTDB%%/*}"
    export REDIS_HOST="${REDIS_HOSTPORT%%:*}"
    export REDIS_PORT="${REDIS_HOSTPORT#*:}"
else
    export REDIS_HOST="${REDIS_HOST:-localhost}"
    export REDIS_PORT="${REDIS_PORT:-6379}"
    export REDIS_USER=""
    export REDIS_PASSWORD=""
    export REDIS_URL="redis://${REDIS_HOST}:${REDIS_PORT}/0"
fi

# Database configuration - supports DATABASE_URL or individual vars
export DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${DB_PORT:-5432}"
export DB_NAME="${DB_NAME:-vexa}"
export DB_USER="${DB_USER:-postgres}"
export DB_PASSWORD="${DB_PASSWORD:-}"

# Build DATABASE_URL if not provided directly, OR parse it if provided
if [ -z "$DATABASE_URL" ]; then
    if [ -n "$DB_PASSWORD" ]; then
        export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
    else
        export DATABASE_URL="postgresql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
    fi
else
    # Fix postgres:// to postgresql:// (SQLAlchemy requirement)
    export DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql:\/\/}"

    # Parse DATABASE_URL into individual vars (required by shared_models)
    # Format: postgresql://user:password@host:port/dbname?params
    # Remove query params first
    DB_URL_BASE="${DATABASE_URL%%\?*}"
    # Extract components using parameter expansion
    DB_URL_NO_SCHEME="${DB_URL_BASE#*://}"
    # Get user:password@host:port/dbname
    DB_USERPASS="${DB_URL_NO_SCHEME%%@*}"
    DB_HOSTPORTDB="${DB_URL_NO_SCHEME#*@}"
    # Parse user and password
    if [[ "$DB_USERPASS" == *":"* ]]; then
        export DB_USER="${DB_USERPASS%%:*}"
        export DB_PASSWORD="${DB_USERPASS#*:}"
    else
        export DB_USER="$DB_USERPASS"
    fi
    # Parse host:port/dbname
    DB_HOSTPORT="${DB_HOSTPORTDB%%/*}"
    export DB_NAME="${DB_HOSTPORTDB#*/}"
    if [[ "$DB_HOSTPORT" == *":"* ]]; then
        export DB_HOST="${DB_HOSTPORT%%:*}"
        export DB_PORT="${DB_HOSTPORT#*:}"
    else
        export DB_HOST="$DB_HOSTPORT"
    fi
    
    # Extract sslmode from DATABASE_URL if present
    if [[ "$DATABASE_URL" == *"sslmode="* ]]; then
        SSL_MODE_PARAM="${DATABASE_URL##*sslmode=}"
        SSL_MODE_PARAM="${SSL_MODE_PARAM%%&*}"
        export DB_SSL_MODE="$SSL_MODE_PARAM"
    fi
fi

# Export DB_SSL_MODE: use provided value or default to "disable"
export DB_SSL_MODE="${DB_SSL_MODE:-disable}"

export LOG_LEVEL="${LOG_LEVEL:-info}"
export DEVICE_TYPE="${DEVICE_TYPE:-remote}"
export WHISPER_BACKEND="${WHISPER_BACKEND:-remote}"
export WHISPER_MODEL_SIZE="${WHISPER_MODEL_SIZE:-tiny}"
export DISPLAY="${DISPLAY:-:99}"

echo "Configuration:"
echo "  - Redis URL: ${REDIS_URL}"
echo "  - Database URL: ${DATABASE_URL}"
echo "  - Database SSL Mode: ${DB_SSL_MODE}"
echo "  - Whisper Model: ${WHISPER_MODEL_SIZE}"
echo "  - Device Type: ${DEVICE_TYPE}"
echo "  - Log Level: ${LOG_LEVEL}"
echo "  - Storage Backend: ${STORAGE_BACKEND:-local}"
echo "  - Local Storage Dir: ${LOCAL_STORAGE_DIR:-/var/lib/vexa/recordings}"
echo ""

# -----------------------------------------------------------------------------
# Wait for PostgreSQL
# -----------------------------------------------------------------------------

if [ -n "$DB_HOST" ] && [ "$DB_HOST" != "localhost" ]; then
    echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."

    max_attempts=30
    attempt=0

    while [ $attempt -lt $max_attempts ]; do
        if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q 2>/dev/null; then
            echo "PostgreSQL is ready!"
            break
        fi

        attempt=$((attempt + 1))
        echo "  Attempt $attempt/$max_attempts - PostgreSQL not ready, waiting..."
        sleep 2
    done

    if [ $attempt -eq $max_attempts ]; then
        echo "WARNING: Could not connect to PostgreSQL after $max_attempts attempts"
        echo "         Services may fail to start properly"
    fi
    echo ""
fi

# -----------------------------------------------------------------------------
# Setup Internal Redis (if using localhost)
# -----------------------------------------------------------------------------

if [ "$USE_INTERNAL_REDIS" = "true" ]; then
    echo "Using internal Redis server (will be started by supervisor)..."
    echo "  Setting up Redis data directory..."
    mkdir -p /var/lib/redis
    mkdir -p /var/run/redis
    chmod 755 /var/lib/redis
    chmod 755 /var/run/redis
    echo "  Redis will start automatically via supervisor"
    echo ""
else
    # -----------------------------------------------------------------------------
    # Wait for External Redis
    # -----------------------------------------------------------------------------
    echo "Waiting for external Redis at ${REDIS_HOST}:${REDIS_PORT}..."

    max_attempts=30
    attempt=0

    # Build redis-cli command with optional auth
    REDIS_CLI_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT"
    if [ -n "$REDIS_PASSWORD" ]; then
        REDIS_CLI_CMD="$REDIS_CLI_CMD -a $REDIS_PASSWORD --no-auth-warning"
    fi

    while [ $attempt -lt $max_attempts ]; do
        if $REDIS_CLI_CMD ping 2>/dev/null | grep -q PONG; then
            echo "Redis is ready!"
            break
        fi

        attempt=$((attempt + 1))
        echo "  Attempt $attempt/$max_attempts - Redis not ready, waiting..."
        sleep 2
    done

    if [ $attempt -eq $max_attempts ]; then
        echo "WARNING: Could not connect to Redis after $max_attempts attempts"
        echo "         Services may fail to start properly"
    fi
    echo ""
fi

# -----------------------------------------------------------------------------
# Database Migrations
# -----------------------------------------------------------------------------

echo "Checking database migrations..."
cd /app/transcription-collector

# Check if database is accessible and run migrations
if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q 2>/dev/null; then
    # Check if alembic_version table exists (database is managed by alembic)
    if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -t -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version';" 2>/dev/null | grep -q 1; then
        echo "  Alembic-managed database detected, running migrations..."
        alembic -c /app/alembic.ini upgrade head || {
            echo "  WARNING: Migration failed, database may already be up to date"
        }
    elif PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -t -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'meetings';" 2>/dev/null | grep -q 1; then
        echo "  Legacy database detected, stamping and migrating..."
        alembic -c /app/alembic.ini stamp base || true
        alembic -c /app/alembic.ini upgrade head || {
            echo "  WARNING: Migration failed"
        }
    else
        echo "  Fresh database detected, initializing schema..."
        python3 -c "
import asyncio
from shared_models.database import init_db
asyncio.run(init_db())
print('Database schema initialized')
" || {
            echo "  WARNING: Schema initialization failed"
        }
        # Stamp with the latest migration
        alembic -c /app/alembic.ini stamp head 2>/dev/null || true
    fi
else
    echo "  WARNING: Database not accessible, skipping migrations"
fi

cd /app
echo ""

# -----------------------------------------------------------------------------
# Create Required Directories
# -----------------------------------------------------------------------------

echo "Creating required directories..."
mkdir -p /var/log/supervisor
mkdir -p /var/log/vexa-bots
mkdir -p /var/run
mkdir -p /var/lib/redis
mkdir -p /var/run/redis
chmod 755 /var/lib/redis
chmod 755 /var/run/redis
# Recording storage directories
mkdir -p "${LOCAL_STORAGE_DIR:-/var/lib/vexa/recordings}"
mkdir -p /var/lib/vexa/recordings/spool
echo "  Done"
echo ""

# -----------------------------------------------------------------------------
# Verify Bot Script
# -----------------------------------------------------------------------------

echo "Verifying bot script..."
if [ -f "/app/vexa-bot/dist/docker.js" ]; then
    echo "  Bot script found at /app/vexa-bot/dist/docker.js"
else
    echo "  WARNING: Bot script not found!"
    echo "  Bot functionality may not work properly"
fi
echo ""

# -----------------------------------------------------------------------------
# Verify Database Connection
# -----------------------------------------------------------------------------

echo "Verifying database connection..."
if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q 2>/dev/null; then
    # Test actual database connection with a simple query
    if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -c "SELECT 1;" >/dev/null 2>&1; then
        echo "  ✅ Database connection successful"
    else
        echo "  ❌ ERROR: Cannot connect to database '$DB_NAME'"
        echo "     Connection parameters: $DB_USER@$DB_HOST:$DB_PORT"
        echo "     Please verify DATABASE_URL and database credentials"
        exit 1
    fi
else
    echo "  ❌ ERROR: PostgreSQL server at $DB_HOST:$DB_PORT is not reachable"
    echo "     Please ensure PostgreSQL is running and accessible"
    exit 1
fi
echo ""

# -----------------------------------------------------------------------------
# Verify Transcription Service
# -----------------------------------------------------------------------------

echo "Verifying transcription service..."
if [ "${SKIP_TRANSCRIPTION_CHECK:-false}" = "true" ]; then
    echo "  ⚠️ Skipping transcription service verification (SKIP_TRANSCRIPTION_CHECK=true)"
    echo "     Ensuring minimal variables are set..."
    
    # Still check for URL presence as it's critical, but don't ping it
    TRANSCRIBER_URL="${TRANSCRIBER_URL:-${REMOTE_TRANSCRIBER_URL:-}}"
    if [ -z "$TRANSCRIBER_URL" ]; then
        echo "  ❌ ERROR: TRANSCRIBER_URL (or REMOTE_TRANSCRIBER_URL) is not set"
        exit 1
    fi
    
    TRANSCRIBER_API_KEY="${TRANSCRIBER_API_KEY:-${REMOTE_TRANSCRIBER_API_KEY:-}}"
    if [ -z "$TRANSCRIBER_API_KEY" ]; then
         echo "  ❌ ERROR: TRANSCRIBER_API_KEY (or REMOTE_TRANSCRIBER_API_KEY) is not set"
         exit 1
    fi
    
    echo "  ✅ Transcription configuration presence verified (connectivity check skipped)"
else
    # Standard verification logic
    TRANSCRIBER_URL="${TRANSCRIBER_URL:-${REMOTE_TRANSCRIBER_URL:-}}"
    if [ -z "$TRANSCRIBER_URL" ]; then
        echo "  ❌ ERROR: TRANSCRIBER_URL (or REMOTE_TRANSCRIBER_URL) is not set"
        echo "     This is required for transcription functionality"
        exit 1
    fi

TRANSCRIBER_API_KEY="${TRANSCRIBER_API_KEY:-${REMOTE_TRANSCRIBER_API_KEY:-}}"
if [ -z "$TRANSCRIBER_API_KEY" ]; then
    echo "  ❌ ERROR: TRANSCRIBER_API_KEY (or REMOTE_TRANSCRIBER_API_KEY) is not set"
    echo "     This is required for transcription service authentication"
    exit 1
fi

# Extract base URL (remove path)
# Handle both http://host:port/path and https://host:port/path
if [[ "$TRANSCRIBER_URL" =~ ^(https?://[^/]+) ]]; then
    BASE_URL="${BASH_REMATCH[1]}"
else
    echo "  ❌ ERROR: Invalid TRANSCRIBER_URL format: $TRANSCRIBER_URL"
    echo "     Expected format: http://host:port/path or https://host:port/path"
    exit 1
fi

# Use curl to check service availability and API key
if ! command -v curl >/dev/null 2>&1; then
    echo "  ❌ ERROR: curl is not available - cannot verify transcription service"
    echo "     URL configured: $TRANSCRIBER_URL"
    exit 1
fi

# Try health endpoint first (usually doesn't require auth, but we'll try with auth too)
HEALTH_URL="${BASE_URL}/health"
if [[ "$TRANSCRIBER_URL" == *"/health"* ]]; then
    HEALTH_URL="$TRANSCRIBER_URL"
fi

# Check if service is reachable (try health endpoint without auth first)
SERVICE_REACHABLE=false
if curl -s -f --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    SERVICE_REACHABLE=true
elif curl -s -f --max-time 5 "$BASE_URL" >/dev/null 2>&1; then
    SERVICE_REACHABLE=true
fi

if [ "$SERVICE_REACHABLE" = "false" ]; then
    echo "  ❌ ERROR: Cannot reach transcription service at $BASE_URL"
    echo "     Please ensure the transcription service is running and accessible"
    exit 1
fi

# Verify API key by making an authenticated request
# Try health endpoint with auth, or if that fails, try a minimal API call
API_KEY_VALID=false
HTTP_CODE=0

# Try health endpoint with Authorization header
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -H "Authorization: Bearer ${TRANSCRIBER_API_KEY}" \
    "$HEALTH_URL" 2>/dev/null)

# Health endpoint might not require auth, so check if we get 200 or 401/403
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
    # If we get 401/403, it means service is reachable but auth might be required
    # If we get 200, service is working
    API_KEY_VALID=true
fi

# If health endpoint doesn't validate auth, try the actual transcription endpoint
# Send a minimal valid request to verify API key
if [ "$API_KEY_VALID" = "false" ] || [ "$HTTP_CODE" = "200" ]; then
    # Create a minimal test file for the request
    TEST_FILE=$(mktemp)
    echo "test" > "$TEST_FILE"
    
    # Try a minimal request to the transcription endpoint to verify API key
    # We expect either 200 (success) or 400/422 (bad request, but auth passed)
    # vs 401/403 (auth failed) or connection error
    TRANS_RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" --max-time 5 \
        -X POST \
        -H "Authorization: Bearer ${TRANSCRIBER_API_KEY}" \
        -F "file=@${TEST_FILE}" \
        -F "model=test" \
        "$TRANSCRIBER_URL" 2>/dev/null)
    
    TRANS_HTTP_CODE=$(echo "$TRANS_RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
    TRANS_BODY=$(echo "$TRANS_RESPONSE" | grep -v "HTTP_CODE")
    
    # Clean up temp file
    rm -f "$TEST_FILE"
    
    # If we get 400/422, check if it's an auth error or just invalid request
    if [ "$TRANS_HTTP_CODE" = "401" ] || [ "$TRANS_HTTP_CODE" = "403" ]; then
        echo "  ❌ ERROR: Invalid transcription service API key"
        echo "     Please verify TRANSCRIBER_API_KEY is correct"
        exit 1
    elif echo "$TRANS_BODY" | grep -qi "invalid.*token\|invalid.*api.*key\|forbidden\|unauthorized"; then
        echo "  ❌ ERROR: Invalid transcription service API key"
        echo "     Please verify TRANSCRIBER_API_KEY is correct"
        exit 1
    elif [ "$TRANS_HTTP_CODE" = "400" ] || [ "$TRANS_HTTP_CODE" = "422" ] || [ "$TRANS_HTTP_CODE" = "200" ]; then
        # 400/422 with invalid request format is OK - means auth passed
        # 200 means success
        API_KEY_VALID=true
    fi
fi

if [ "$API_KEY_VALID" = "true" ]; then
    echo "  ✅ Transcription service is reachable and API key is valid"
else
    echo "  ❌ ERROR: Cannot verify transcription service API key"
    echo "     Service is reachable but authentication verification failed"
    exit 1
fi
fi
echo ""

# -----------------------------------------------------------------------------
# PulseAudio & ALSA Configuration
# -----------------------------------------------------------------------------

echo "Configuring PulseAudio and ALSA..."

# Configure ALSA to route through PulseAudio (needed before PA starts)
cat > /root/.asoundrc <<'ALSA_EOF'
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
ALSA_EOF
echo "  ALSA configured to use PulseAudio"

# Create a post-start script for PulseAudio sink setup
# This runs after supervisord starts PulseAudio
cat > /usr/local/bin/setup-pulseaudio-sinks.sh <<'PA_EOF'
#!/bin/bash
# Wait for PulseAudio to be ready
for i in $(seq 1 15); do
    if pactl info >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! pactl info >/dev/null 2>&1; then
    echo "[PulseAudio Setup] ERROR: PulseAudio not available after 15s"
    exit 1
fi

echo "[PulseAudio Setup] Creating null sink for audio capture..."
pactl load-module module-null-sink sink_name=zoom_sink sink_properties=device.description="ZoomAudioSink" 2>/dev/null || true

echo "[PulseAudio Setup] Creating TTS sink for voice agent..."
pactl load-module module-null-sink sink_name=tts_sink sink_properties=device.description="TTSAudioSink" 2>/dev/null || true

echo "[PulseAudio Setup] Creating virtual microphone from TTS sink monitor..."
pactl load-module module-remap-source master=tts_sink.monitor source_name=virtual_mic source_properties=device.description="VirtualMicrophone" 2>/dev/null || true
pactl set-default-source virtual_mic 2>/dev/null || true

echo "[PulseAudio Setup] Done - sinks configured"
PA_EOF
chmod +x /usr/local/bin/setup-pulseaudio-sinks.sh
echo "  PulseAudio sink setup script created"
echo ""

# -----------------------------------------------------------------------------
# Start Services
# -----------------------------------------------------------------------------

echo "=============================================="
echo "  Starting Vexa Services via Supervisor"
echo "=============================================="
echo ""
echo "Service Endpoints:"
echo "  - API Gateway:    http://localhost:8056"
echo "  - Admin API:      http://localhost:8057"
echo "  - API Docs:       http://localhost:8056/docs"
echo ""

# Execute the command passed to the container (supervisord by default)
exec "$@"
