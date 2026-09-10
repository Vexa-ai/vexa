#!/usr/bin/env bash
# mint-dev-env.sh — seed deploy/compose/.env from .env.example and MINT every secret the stack
# refuses to run without. Since 0.12.27 the services refuse to boot on an empty or published
# placeholder for these keys (config.v1 `forbidden_values`); a fresh checkout therefore needs real
# values before `docker compose up`. CI's value leg and release-validate call this instead of a bare
# `cp`; self-hosters may call it too — it never overwrites an existing non-empty value.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${1:-$here/.env}"
[ -f "$env_file" ] || cp "$here/.env.example" "$env_file"
mint() { openssl rand -hex 32; }
for key in INTERNAL_API_SECRET VEXA_FLOWS_API_KEY VEXA_FLOWS_TIMELINE_KEY; do
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
