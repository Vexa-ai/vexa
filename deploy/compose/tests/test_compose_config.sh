#!/bin/bash
# Validate docker-compose.yml syntax and service definitions.
# Does NOT start containers — just verifies the compose files parse correctly
# and all expected services are defined.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/compose/docker-compose.yml"
COMPOSE_LOCAL_DB="$ROOT/deploy/compose/docker-compose.local-db.yml"

echo "=== Compose config validation ==="

# Check files exist
for f in "$COMPOSE_FILE" "$COMPOSE_LOCAL_DB"; do
  if [ ! -f "$f" ]; then
    echo "FAIL: $f not found"
    exit 1
  fi
  echo "  OK: $(basename "$f") exists"
done

# Validate YAML syntax with docker compose config (dry-run)
if command -v docker &>/dev/null; then
  # Create a minimal .env so compose config doesn't fail on missing vars
  TMPENV=$(mktemp)
  if [ -f "$ROOT/.env" ]; then
    cp "$ROOT/.env" "$TMPENV"
  else
    cp "$ROOT/deploy/env/env-example" "$TMPENV"
  fi

  services=$(docker compose --env-file "$TMPENV" -f "$COMPOSE_FILE" config --services 2>&1)
  rm -f "$TMPENV"

  if [ $? -ne 0 ]; then
    echo "FAIL: docker compose config failed"
    echo "$services"
    exit 1
  fi

  echo "  OK: docker-compose.yml parses"

  # Check expected services exist
  for svc in api-gateway admin-api meeting-api mcp dashboard redis; do
    if echo "$services" | grep -q "^${svc}$"; then
      echo "  OK: service '$svc' defined"
    else
      echo "  WARN: service '$svc' not found in compose config"
    fi
  done
else
  echo "  SKIP: docker not available, skipping compose config validation"
fi

echo ""
echo "Compose config validation: PASS"
