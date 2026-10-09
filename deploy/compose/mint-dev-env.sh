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
