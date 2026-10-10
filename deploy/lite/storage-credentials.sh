#!/bin/sh
# storage-credentials.sh <env-file> — make sure <env-file> holds the storage sidecar's root
# credentials, MINIO_ACCESS_KEY and MINIO_SECRET_KEY, and that neither is a value published in this
# repository. Each one that is unset, empty or published is minted and written back; a value already
# set is kept. `make -C deploy/lite up` runs this before it starts the storage sidecar, and recreates
# the sidecar when its credentials differ, so an install that ran with the old published pair moves to
# the minted one (the data volume and every object in it stay). Prints which keys it minted, never a
# value. The refused list is the one compose's storage, Lite's entrypoint and mint-dev-env.sh refuse.
set -eu
env_file="${1:?usage: storage-credentials.sh <env-file>}"
[ -f "$env_file" ] || : > "$env_file"

published() {
    case "$1" in
        ""|vexa-access-key|vexa-secret-key|minioadmin|changeme|change-me|CHANGE-ME|default|secret|password) return 0;;
    esac
    return 1
}

value() {
    grep -E "^$1=" "$env_file" 2>/dev/null | head -1 | cut -d= -f2- \
        | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'
}

for key in MINIO_ACCESS_KEY MINIO_SECRET_KEY; do
    if published "$(value "$key")"; then
        if [ "$key" = MINIO_ACCESS_KEY ]; then v="vexa-$(openssl rand -hex 12)"; else v="$(openssl rand -hex 32)"; fi
        if grep -qE "^${key}=" "$env_file"; then
            sed -i.bak "s|^${key}=.*|${key}=${v}|" "$env_file" && rm -f "$env_file.bak"
        else
            printf '\n%s=%s\n' "$key" "$v" >> "$env_file"
        fi
        echo "  Minted the storage root ${key} into $env_file"
    fi
done
