#!/usr/bin/env bash
# mint-dev-env.sh — seed deploy/compose/.env from .env.example and MINT every secret the stack
# refuses to run without. Since 0.12.27 the services refuse to boot on an empty or published
# placeholder for these keys (config.v1 `forbidden_values`), compose itself requires NEXTAUTH_SECRET,
# and the terminal refuses a short or published one; a fresh checkout therefore needs real values
# before `docker compose up`. `make up` / `make all` / `make dev`, CI's value leg and
# release-validate call this instead of a bare `cp` — it never overwrites an existing non-empty
# value, except a NEXTAUTH_SECRET this repository once shipped as its default.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${1:-$here/.env}"
[ -f "$env_file" ] || cp "$here/.env.example" "$env_file"
mint() { openssl rand -hex 32; }
# A published default is no secret: NEXTAUTH_SECRET holding one this repository once shipped is
# minted afresh like an empty one (the terminal refuses to start on it either way).
if grep -qE "^NEXTAUTH_SECRET=\s*(dev-nextauth-secret|vexa-dev-nextauth-secret|vexa-dev-secret)\s*$" "$env_file"; then
  sed -i.bak "s|^NEXTAUTH_SECRET=.*|NEXTAUTH_SECRET=|" "$env_file" && rm -f "$env_file.bak"
  echo "replacing NEXTAUTH_SECRET (a published default)"
fi
# The same for the dispatch signing key's old default. Nothing verifies its tokens yet, so a new value
# changes nothing a running stack depends on.
if grep -qE "^VEXA_DISPATCH_SIGNING_KEY=\s*dev-dispatch-signing-key\s*$" "$env_file"; then
  sed -i.bak "s|^VEXA_DISPATCH_SIGNING_KEY=.*|VEXA_DISPATCH_SIGNING_KEY=|" "$env_file" && rm -f "$env_file.bak"
  echo "replacing VEXA_DISPATCH_SIGNING_KEY (a published default)"
fi
# The storage root pair (the `storage` service's root key, meeting-api's MINIO_*). The pair this file
# and compose used to default to is published: an install that ran with it gets a new pair, and the old
# one is kept as LEGACY_MINIO_* for `make migrate-storage` from an older MinIO that still uses it. The
# storage service takes its root key from its environment, so the next `up` restarts it with the new
# pair over the same volume. A value already set and not published is kept; values are never printed.
storage_published() {
  case "$1" in ""|vexa-access-key|vexa-secret-key|minioadmin|changeme|change-me|CHANGE-ME|default|secret|password) return 0;; esac
  return 1
}
env_value() { grep -E "^$1=" "$env_file" | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//'; }
for key in MINIO_ACCESS_KEY MINIO_SECRET_KEY; do
  if grep -qE "^${key}=" "$env_file"; then old="$(env_value "$key")"
  elif [ "$key" = MINIO_ACCESS_KEY ]; then old=vexa-access-key       # compose's old fallback
  else old=vexa-secret-key; fi
  storage_published "$old" || { echo "kept ${key}"; continue; }
  if [ -n "$old" ] && ! grep -qE "^LEGACY_${key}=" "$env_file"; then
    printf 'LEGACY_%s=%s\n' "$key" "$old" >> "$env_file"
  fi
  if [ "$key" = MINIO_ACCESS_KEY ]; then v="vexa-$(openssl rand -hex 12)"; else v="$(mint)"; fi
  if grep -qE "^${key}=" "$env_file"; then
    sed -i.bak "s|^${key}=.*|${key}=${v}|" "$env_file" && rm -f "$env_file.bak"
  else
    echo "${key}=${v}" >> "$env_file"
  fi
  if [ -n "$old" ]; then echo "replacing ${key} (a published default)"; else echo "minted ${key}"; fi
done
for key in INTERNAL_API_SECRET RUNTIME_API_TOKEN DB_PASSWORD REDIS_PASSWORD VEXA_MCP_DELEGATION_SECRET \
           VEXA_DISPATCH_SIGNING_KEY VEXA_FLOWS_API_KEY VEXA_FLOWS_TIMELINE_KEY NEXTAUTH_SECRET; do
  if grep -qE "^${key}=\s*$" "$env_file"; then
    v="$(mint)"
    # portable in-place edit (GNU and BSD sed)
    sed -i.bak "s|^${key}=.*|${key}=${v}|" "$env_file" && rm -f "$env_file.bak"
    echo "minted ${key}"
  elif ! grep -qE "^${key}=" "$env_file"; then
    echo "${key}=$(mint)" >> "$env_file"; echo "appended ${key}"
  else
    echo "kept ${key}"
  fi
done
