#!/bin/bash
# =============================================================================
# Vexa Monolithic - Container Entrypoint
# =============================================================================
# This script initializes the monolithic Vexa container:
# 1. Detects and validates GPU (if GPU build)
# 2. Waits for external services (PostgreSQL, Redis)
# 3. Runs database migrations
# 4. Starts all services via supervisord
# =============================================================================

set -e

echo "=============================================="
echo "  Vexa Monolithic - Starting Container"
echo "=============================================="
echo ""

# -----------------------------------------------------------------------------
# GPU Detection and Validation
# -----------------------------------------------------------------------------

detect_gpu() {
    echo "Hardware Detection:"
    echo "-------------------"

    # Check if this is a GPU build
    if [ "$BUILD_DEVICE" = "gpu" ]; then
        CUDA_VER="${BUILD_CUDA_VERSION:-12.4}"
        echo "  Build Type: GPU (CUDA ${CUDA_VER})"

        # Check if nvidia-smi is available
        if command -v nvidia-smi &> /dev/null; then
            echo "  NVIDIA Driver: Available"

            # Get GPU information
            GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits 2>/dev/null || echo "")

            if [ -n "$GPU_INFO" ]; then
                echo ""
                echo "  Detected GPU(s):"
                GPU_COUNT=0
                while IFS=',' read -r name memory driver compute; do
                    GPU_COUNT=$((GPU_COUNT + 1))
                    name=$(echo "$name" | xargs)  # trim whitespace
                    memory=$(echo "$memory" | xargs)
                    driver=$(echo "$driver" | xargs)
                    compute=$(echo "$compute" | xargs)
                    echo "    [$((GPU_COUNT-1))] $name"
                    echo "        Memory: ${memory} MB"
                    echo "        Driver: $driver"
                    echo "        Compute Capability: $compute"
                done <<< "$GPU_INFO"
                echo ""
                echo "  Total GPUs: $GPU_COUNT"

                # Validate CUDA availability via Python
                echo ""
                echo "  CUDA Validation:"
                CUDA_AVAILABLE=$(python3 -c "import torch; print('yes' if torch.cuda.is_available() else 'no')" 2>/dev/null || echo "error")

                if [ "$CUDA_AVAILABLE" = "yes" ]; then
                    CUDA_CHECK=$(python3 -c "
import torch
print(f'    PyTorch CUDA: Available')
print(f'    CUDA Version: {torch.version.cuda}')
print(f'    cuDNN Version: {torch.backends.cudnn.version()}')
for i in range(torch.cuda.device_count()):
    props = torch.cuda.get_device_properties(i)
    print(f'    Device {i}: {props.name} (SM {props.major}.{props.minor})')
" 2>&1)
                    echo "$CUDA_CHECK"

                    # Set DEVICE_TYPE to cuda
                    export DEVICE_TYPE="cuda"
                    echo ""
                    echo "  Auto-configured DEVICE_TYPE=cuda"
                else
                    echo "    PyTorch CUDA: NOT AVAILABLE"
                    echo ""
                    echo "  ╔════════════════════════════════════════════════════════════╗"
                    echo "  ║  WARNING: PyTorch cannot access CUDA                       ║"
                    echo "  ╠════════════════════════════════════════════════════════════╣"
                    echo "  ║  nvidia-smi detects GPU but PyTorch CUDA is unavailable.   ║"
                    echo "  ║                                                            ║"
                    echo "  ║  This may happen if:                                       ║"
                    echo "  ║    - CUDA driver version mismatch                          ║"
                    echo "  ║    - GPU architecture not supported by this PyTorch build  ║"
                    echo "  ║                                                            ║"
                    echo "  ║  FALLBACK: Running in CPU mode (slower, ~5-10x)            ║"
                    echo "  ╚════════════════════════════════════════════════════════════╝"
                    echo ""
                    export DEVICE_TYPE="cpu"
                fi
            else
                echo ""
                echo "  ╔════════════════════════════════════════════════════════════╗"
                echo "  ║  WARNING: NO GPU DETECTED                                  ║"
                echo "  ╠════════════════════════════════════════════════════════════╣"
                echo "  ║  nvidia-smi is available but no GPU was found.             ║"
                echo "  ║                                                            ║"
                echo "  ║  Possible causes:                                          ║"
                echo "  ║    - Container started without --gpus flag                 ║"
                echo "  ║    - GPU not properly passed to container                  ║"
                echo "  ║                                                            ║"
                echo "  ║  Fix: docker run --gpus all ...                            ║"
                echo "  ║                                                            ║"
                echo "  ║  FALLBACK: Running in CPU mode (slower transcription)      ║"
                echo "  ╚════════════════════════════════════════════════════════════╝"
                echo ""
                export DEVICE_TYPE="cpu"
            fi
        else
            echo ""
            echo "  ╔════════════════════════════════════════════════════════════╗"
            echo "  ║  WARNING: GPU BUILD WITHOUT GPU ACCESS                     ║"
            echo "  ╠════════════════════════════════════════════════════════════╣"
            echo "  ║  This image was built with DEVICE=gpu but nvidia-smi       ║"
            echo "  ║  is not available in this container.                       ║"
            echo "  ║                                                            ║"
            echo "  ║  Possible causes:                                          ║"
            echo "  ║    - Missing --gpus flag when starting container           ║"
            echo "  ║    - NVIDIA Container Toolkit not installed on host        ║"
            echo "  ║    - No NVIDIA GPU available on host machine               ║"
            echo "  ║                                                            ║"
            echo "  ║  To fix, run with:                                         ║"
            echo "  ║    docker run --gpus all ...                               ║"
            echo "  ║                                                            ║"
            echo "  ║  Or rebuild for CPU:                                       ║"
            echo "  ║    docker build -f Dockerfile.monolithic -t vexa .         ║"
            echo "  ║                                                            ║"
            echo "  ║  FALLBACK: Running in CPU mode (slower, ~5-10x)            ║"
            echo "  ║  Note: GPU image is larger (~8GB vs ~4GB for CPU image)    ║"
            echo "  ╚════════════════════════════════════════════════════════════╝"
            echo ""
            export DEVICE_TYPE="cpu"
        fi
    else
        echo "  Build Type: CPU"
        export DEVICE_TYPE="cpu"

        # Check CPU info
        CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "Unknown")
        CPU_CORES=$(nproc 2>/dev/null || echo "Unknown")
        echo "  CPU: $CPU_MODEL"
        echo "  Cores: $CPU_CORES"
    fi

    echo ""
}

# Run GPU detection
detect_gpu

# -----------------------------------------------------------------------------
# Environment Setup
# -----------------------------------------------------------------------------

# Set defaults for environment variables

# Redis configuration - supports REDIS_URL or individual vars
if [ -n "$REDIS_URL" ]; then
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
    export DB_HOST="${DB_HOSTPORT%%:*}"
    export DB_PORT="${DB_HOSTPORT#*:}"
fi

export LOG_LEVEL="${LOG_LEVEL:-info}"
export WHISPER_MODEL_SIZE="${WHISPER_MODEL_SIZE:-tiny}"
export DISPLAY="${DISPLAY:-:99}"

# Recommend larger models for GPU
if [ "$DEVICE_TYPE" = "cuda" ] && [ "$WHISPER_MODEL_SIZE" = "tiny" ]; then
    echo "TIP: GPU detected with tiny model. Consider using medium or large for better accuracy:"
    echo "     -e WHISPER_MODEL_SIZE=medium"
    echo ""
fi

echo "Configuration:"
echo "  - Redis: ${REDIS_HOST}:${REDIS_PORT}"
echo "  - Database: ${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "  - Whisper Model: ${WHISPER_MODEL_SIZE}"
echo "  - Device Type: ${DEVICE_TYPE}"
echo "  - Log Level: ${LOG_LEVEL}"
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
# Wait for Redis
# -----------------------------------------------------------------------------

if [ -n "$REDIS_HOST" ] && [ "$REDIS_HOST" != "localhost" ]; then
    echo "Waiting for Redis at ${REDIS_HOST}:${REDIS_PORT}..."

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
cd /app

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

echo ""

# -----------------------------------------------------------------------------
# Create Required Directories
# -----------------------------------------------------------------------------

echo "Creating required directories..."
mkdir -p /var/log/supervisor
mkdir -p /var/log/vexa-bots
mkdir -p /var/run
mkdir -p /app/storage/screenshots
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
if [ "$DEVICE_TYPE" = "cuda" ]; then
echo "  - WhisperLive:    GPU-accelerated (${WHISPER_MODEL_SIZE})"
else
echo "  - WhisperLive:    CPU mode (${WHISPER_MODEL_SIZE})"
fi
echo ""

# Execute the command passed to the container (supervisord by default)
exec "$@"
