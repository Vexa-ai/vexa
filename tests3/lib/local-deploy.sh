#!/usr/bin/env bash
# local-deploy.sh — LOCAL=1 path for release-deploy.
#
# Rebuilds images + recreates containers on the dev machine.
# Writes the same `image_tag` markers that the canonical VM path writes.
#
# Usage: local-deploy.sh "<modes-space-separated>"
#   e.g. local-deploy.sh "compose lite"
#
# Outputs (per mode):
#   tests3/.state-<mode>/image_tag = <tag>
#   tests3/.state-<mode>/deploy.log = build + recreate trace
#   deploy/compose/.last-tag = <tag> (for compose)
#
# Tag format: 0.10.6-YYMMDD-HHMM (matches the canonical release-build naming).

set -euo pipefail

MODES="${1:-}"
if [[ -z "$MODES" ]]; then
    echo "FAIL: usage: local-deploy.sh \"<modes-space-separated>\"" >&2
    exit 2
fi

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
LOCAL_ENV_FILE="$ROOT_DIR/deploy/compose/.env"
TAG="$(date +%y%m%d-%H%M)"
# Mirror release-build naming: <version-prefix>-<YYMMDD-HHMM>.
VERSION_PREFIX="$(cat "$ROOT_DIR/deploy/compose/.last-tag" 2>/dev/null | sed 's/-[0-9]*-[0-9]*$//' || echo "0.10.6")"
IMAGE_TAG="${VERSION_PREFIX}-${TAG}"

# LOCAL=1 port-remap: avoid host-port collisions with BUSINESS-workspace
# services that share the dev host (e.g. webhooks/receiver on 9000). Names
# match local-provision.sh's declared ports.env entries; values map to the
# compose docker-compose.yml's documented env var names (MINIO_HOST_PORT /
# MINIO_CONSOLE_HOST_PORT). Export so docker compose picks them up.
export MINIO_HOST_PORT="${MINIO_HOST_PORT:-9100}"
export MINIO_CONSOLE_HOST_PORT="${MINIO_CONSOLE_HOST_PORT:-9101}"

# LOCAL=1 mode order: compose must come BEFORE lite, otherwise lite (in its
# default --network host mode on ports 8056/3000/8057) holds the gateway/
# dashboard/admin ports and compose collides. With compose first, the lite
# step's `docker ps | grep vexa-api-gateway` detects compose-up and switches
# lite to --network vexa-lite-net + alt ports (8156/3100/8157).
sorted_modes=""
for m in $MODES; do
    [ "$m" = "compose" ] && sorted_modes="compose ${sorted_modes}" || sorted_modes="${sorted_modes} $m"
done
MODES="$sorted_modes"

echo "=== LOCAL-DEPLOY: modes=$MODES tag=$IMAGE_TAG ==="
echo "    MINIO_HOST_PORT=$MINIO_HOST_PORT MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT"
echo ""

env_get() {
    local key="$1"
    awk -F= -v k="$key" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$LOCAL_ENV_FILE" 2>/dev/null || true
}

lite_postgres_password() {
    local state="$1"
    local file="$state/postgres_password"
    if [ ! -s "$file" ]; then
        if docker ps -a --format '{{.Names}}' | grep -qx 'vexa-lite-postgres'; then
            docker rm -f vexa-lite-postgres >/dev/null 2>&1 || true
        fi
        umask 077
        if command -v openssl >/dev/null 2>&1; then
            openssl rand -hex 24 > "$file"
        else
            od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$file"
            printf '\n' >> "$file"
        fi
    fi
    tr -d '\n\r' < "$file"
}

env_set_many() {
    python3 - "$LOCAL_ENV_FILE" "$@" <<'PYEOF'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
items = sys.argv[2:]
for item in items:
    key, value = item.split("=", 1)
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text)
    else:
        text += f"\n# LOCAL-override (not in env-example yet)\n{key}={value}\n"
path.write_text(text)
PYEOF
}

ensure_local_env() {
    mkdir -p "$(dirname "$LOCAL_ENV_FILE")"
    if [ ! -f "$LOCAL_ENV_FILE" ]; then
        cp "$ROOT_DIR/deploy/env-example" "$LOCAL_ENV_FILE"
    fi
}

require_local_env_pair() {
    local context="$1"
    LOCAL_TRANSCRIPTION_URL="$(env_get TRANSCRIPTION_SERVICE_URL)"
    LOCAL_TRANSCRIPTION_TOKEN="$(env_get TRANSCRIPTION_SERVICE_TOKEN)"
    if [ -z "$LOCAL_TRANSCRIPTION_URL" ] || [ -z "$LOCAL_TRANSCRIPTION_TOKEN" ]; then
        echo "$context: deploy/compose/.env is the LOCAL SSOT; set TRANSCRIPTION_SERVICE_URL and TRANSCRIPTION_SERVICE_TOKEN there before local deploy" >&2
        exit 1
    fi
}

for mode in $MODES; do
    case "$mode" in
        compose)
            STATE="$ROOT_DIR/tests3/.state-compose"
            mkdir -p "$STATE"
            LOG="$STATE/deploy.log"
            echo "  compose: rebuild (--no-cache) + recreate ..."
            {
                echo "=== LOCAL-DEPLOY compose · $(date -Iseconds) · tag=$IMAGE_TAG ==="
                cd "$ROOT_DIR/deploy/compose"
                # Build the runtime bot image (separate from compose services
                # since vexa-bot is spawned by runtime-api, not in docker-compose.yml).
                # Without this, runtime-api spawns containers with empty BROWSER_IMAGE
                # and bot dispatch silently 0-exits with "exec entrypoint.sh: no such file".
                make --no-print-directory build-bot-image BUILD_TAG="$IMAGE_TAG" 2>&1

                # ── .env generation: SSOT-based ────────────────────────────
                # `deploy/compose/.env` is the LOCAL deployment SSOT. It is
                # initialized once from deploy/env-example when absent, then
                # local-deploy only updates derived LOCAL values such as image
                # tag, ports, dashboard API key, and MinIO public endpoint.
                # Required operator parameters (notably transcription URL/token)
                # must already be present in this file; no caller-env, host-file,
                # generated-env, or fake-token fallback is allowed.
                #
                # Why this shape: previously local-deploy.sh hand-curated a
                # 4-line .env that silently omitted everything not on its
                # list (BROWSER_IMAGE → empty → bot exec failure;
                # TRANSCRIPTION_SERVICE_URL → empty → silent no-transcript
                # bug). Drift was the default mode. With env-example as the
                # SSOT, new required vars added to env-example are visible
                # without per-cycle whack-a-mole.
                ensure_local_env
                require_local_env_pair "compose"

                # If validate/bootstrap has already minted a tests3 API token,
                # seed the dashboard with that same token so the human
                # validation UI shows the registry-created meetings. Without
                # this, /api/vexa/meetings falls back to an empty VEXA_API_KEY
                # and the dashboard renders "no meetings" even though /bots
                # with tests3/.state-compose/api_token has rows.
                LOCAL_DASHBOARD_API_KEY=""
                if [ -r "$ROOT_DIR/tests3/.state-compose/api_token" ]; then
                    LOCAL_DASHBOARD_API_KEY="$(tr -d '\n\r' < "$ROOT_DIR/tests3/.state-compose/api_token")"
                fi

                env_set_many \
                    "IMAGE_TAG=$IMAGE_TAG" \
                    "BROWSER_IMAGE=vexaai/vexa-bot:$IMAGE_TAG" \
                    "MINIO_HOST_PORT=$MINIO_HOST_PORT" \
                    "MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT" \
                    "MINIO_PUBLIC_ENDPOINT=http://localhost:$MINIO_HOST_PORT" \
                    "VEXA_API_KEY=$LOCAL_DASHBOARD_API_KEY"

                IMAGE_TAG="$IMAGE_TAG" docker compose build --no-cache 2>&1
                IMAGE_TAG="$IMAGE_TAG" docker compose up -d --force-recreate 2>&1

                if echo "$LOCAL_TRANSCRIPTION_URL" | grep -qE '^https?://transcription-lb(/|:|$)'; then
                    # Compose may recreate `vexa_vexa` during `up`, which drops
                    # ad-hoc external attachments. Attach after `up` and verify
                    # from inside the stack that the SSOT URL is resolvable.
                    docker network connect vexa_vexa transcription-lb 2>/dev/null || true
                    docker exec vexa-meeting-api-1 getent hosts transcription-lb >/dev/null 2>&1 || {
                        echo "compose: TRANSCRIPTION_SERVICE_URL=$LOCAL_TRANSCRIPTION_URL but transcription-lb is not resolvable from vexa_vexa" >&2
                        echo "compose: attach transcription-lb to vexa_vexa or use a URL reachable from bot containers" >&2
                        exit 1
                    }
                fi
            } > "$LOG" 2>&1 || { echo "  compose: build/up FAILED — see $LOG" >&2; tail -20 "$LOG" >&2; exit 1; }
            echo "$IMAGE_TAG" > "$STATE/image_tag"
            echo "compose" > "$STATE/deploy_mode"
            echo "http://localhost:8056" > "$STATE/gateway_url"
            echo "http://localhost:8057" > "$STATE/admin_url"
            echo "http://localhost:3001" > "$STATE/dashboard_url"
            cat > "$STATE/ports.env" <<'PORTS'
GATEWAY_PORT=8056
ADMIN_PORT=8057
DASHBOARD_PORT=3001
RUNTIME_PORT=8090
MCP_PORT=18888
MINIO_API_PORT=9100
MINIO_CONSOLE_PORT=9101
POSTGRES_PORT=5438
PORTS
            echo "$IMAGE_TAG" > "$ROOT_DIR/deploy/compose/.last-tag"
            # Health check key endpoints.
            ok=0; total=0
            for spec in "8056:gateway" "8057:admin" "3001:dashboard" "8090:runtime"; do
                port="${spec%%:*}"; name="${spec##*:}"
                total=$((total+1))
                if curl -sf -o /dev/null --max-time 5 "http://localhost:${port}/" 2>/dev/null \
                   || curl -sf -o /dev/null --max-time 5 "http://localhost:${port}/docs" 2>/dev/null \
                   || curl -sf -o /dev/null --max-time 5 "http://localhost:${port}/health" 2>/dev/null; then
                    ok=$((ok+1))
                    echo "    ok   $name (localhost:$port)"
                else
                    echo "    WARN $name (localhost:$port) not responding within 5s"
                fi
            done
            echo "  compose: deployed ($ok/$total endpoints healthy on tag $IMAGE_TAG)"
            ;;
        lite)
            STATE="$ROOT_DIR/tests3/.state-lite"
            mkdir -p "$STATE"
            LITE_POSTGRES_PASSWORD="$(lite_postgres_password "$STATE")"
            LOG="$STATE/deploy.log"
            echo "  lite: rebuild + recreate ..."
            {
                echo "=== LOCAL-DEPLOY lite · $(date -Iseconds) · tag=$IMAGE_TAG ==="
                cd "$ROOT_DIR/deploy/lite"
                TAG="$IMAGE_TAG" make build 2>&1
                # Recreate the lite container (single-container deploy).
                docker stop vexa-lite 2>/dev/null || true
                docker rm vexa-lite 2>/dev/null || true
                # Use the bridge network we provisioned for lite (vexa-lite-net) so
                # compose + lite can coexist without port conflicts. If lite is the
                # only stack, --network host (canonical Makefile path) works too —
                # we prefer the bridge for the dev-loop case where both stacks run.
                if docker ps --format '{{.Names}}' | grep -q '^vexa-' 2>/dev/null \
                   && docker ps --format '{{.Names}}' | grep -q 'vexa-api-gateway'; then
                    NETWORK_FLAG="--network vexa-lite-net"
                    GATEWAY_HOST_PORT=8156; DASHBOARD_HOST_PORT=3100; ADMIN_HOST_PORT=8157
                    DB_HOST=vexa-lite-postgres
                else
                    NETWORK_FLAG="--network host"
                    GATEWAY_HOST_PORT=8056; DASHBOARD_HOST_PORT=3000; ADMIN_HOST_PORT=8057
                    DB_HOST=localhost
                fi
                # Keep lite's sidecar Postgres alive when we run on the bridge
                # network next to compose. If the container already exists,
                # restart it so prior seed data survives; otherwise create it.
                if [ "$DB_HOST" = "vexa-lite-postgres" ]; then
                    if docker ps -a --format '{{.Names}}' | grep -qx 'vexa-lite-postgres'; then
                        docker start vexa-lite-postgres 2>&1
                    else
                        docker run -d --name vexa-lite-postgres --network vexa-lite-net \
                            -e POSTGRES_DB=vexa \
                            -e POSTGRES_USER=postgres \
                            -e POSTGRES_PASSWORD="$LITE_POSTGRES_PASSWORD" \
                            postgres:17-alpine \
                            -c idle_in_transaction_session_timeout=60000 2>&1
                    fi
                    for i in $(seq 1 24); do
                        docker exec vexa-lite-postgres pg_isready -U postgres -d vexa -q 2>/dev/null && break
                        sleep 2
                    done
                fi
                ensure_local_env
                require_local_env_pair "lite"
                tx_url="$LOCAL_TRANSCRIPTION_URL"
                tx_token="$LOCAL_TRANSCRIPTION_TOKEN"
                if echo "$tx_url" | grep -qE '^https?://transcription-lb(/|:|$)'; then
                    docker network connect vexa-lite-net transcription-lb 2>/dev/null || true
                fi
                mkdir -p "$STATE/recordings"
                docker run -d --name vexa-lite \
                    $NETWORK_FLAG --shm-size=2g \
                    $([ "$NETWORK_FLAG" = "--network vexa-lite-net" ] && \
                      echo "-p 127.0.0.1:${GATEWAY_HOST_PORT}:8056 -p 127.0.0.1:${DASHBOARD_HOST_PORT}:3000 -p 127.0.0.1:${ADMIN_HOST_PORT}:8057") \
                    -v "$STATE/recordings:/var/lib/vexa/recordings" \
                    -e DATABASE_URL="postgresql://postgres:${LITE_POSTGRES_PASSWORD}@${DB_HOST}:5432/vexa" \
                    -e DB_HOST="$DB_HOST" -e DB_PORT=5432 -e DB_NAME=vexa \
                    -e DB_USER=postgres -e DB_PASSWORD="$LITE_POSTGRES_PASSWORD" -e DB_SSL_MODE=disable \
                    -e REDIS_URL=redis://localhost:6379/0 \
                    -e ADMIN_API_TOKEN=changeme \
                    -e VEXA_AUTH_COOKIE_NAME=vexa-token-lite \
                    -e VEXA_USER_INFO_COOKIE_NAME=vexa-user-info-lite \
                    -e SKIP_TRANSCRIPTION_CHECK=true \
                    -e TRANSCRIPTION_SERVICE_URL="$tx_url" \
                    -e TRANSCRIPTION_SERVICE_TOKEN="$tx_token" \
                    "vexa-lite:$IMAGE_TAG" 2>&1
            } > "$LOG" 2>&1 || { echo "  lite: build/up FAILED — see $LOG" >&2; tail -20 "$LOG" >&2; exit 1; }
            echo "$IMAGE_TAG" > "$STATE/image_tag"
            echo "lite" > "$STATE/deploy_mode"
            echo "http://localhost:${GATEWAY_HOST_PORT}" > "$STATE/gateway_url"
            echo "http://localhost:${ADMIN_HOST_PORT}" > "$STATE/admin_url"
            echo "http://localhost:${DASHBOARD_HOST_PORT}" > "$STATE/dashboard_url"
            cat > "$STATE/ports.env" <<PORTS
GATEWAY_PORT=${GATEWAY_HOST_PORT}
ADMIN_PORT=${ADMIN_HOST_PORT}
DASHBOARD_PORT=${DASHBOARD_HOST_PORT}
PORTS
            echo "  lite: deployed (tag $IMAGE_TAG)"
            ;;
        helm)
            echo "  helm: LOCAL=1 not supported for helm mode (requires LKE cluster) — skipping" >&2
            ;;
        *)
            echo "  WARN: unknown mode '$mode' — skipping" >&2
            ;;
    esac
done

echo ""
echo "  LOCAL-DEPLOY done. Next: make release-validate LOCAL=1 SCOPE=<scope>."
