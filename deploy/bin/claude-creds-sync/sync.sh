#!/bin/sh
# Keep ~/.claude/.credentials.json (the HOST_CLAUDE_CREDENTIALS mount) in sync
# with Claude Code's real credential store.
#
# Why: agent containers bind-mount this file read-only. On macOS the CLI's
# source of truth is the login Keychain, so the file is a one-time export that
# silently expires every ~8-12 h -> agent chat fails with 401. On Linux (and
# Windows via WSL2) the CLI reads and refreshes the file itself, so there is
# nothing to sync.
#
# Behavior: compare-and-write. The file is only rewritten when the Keychain
# content differs, and always via truncate-write into the SAME file — the
# bind-mount follows the inode; replacing the file would detach it.
set -eu

FILE="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"
LOG="$HOME/.claude/creds-sync.log"

case "$(uname -s)" in
  Darwin) ;;
  *)
    # Linux / WSL: the file is authoritative; nothing to do.
    exit 0
    ;;
esac

NEW=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null) || exit 0
[ -n "$NEW" ] || exit 0

CUR=$(cat "$FILE" 2>/dev/null || true)
if [ "$NEW" != "$CUR" ]; then
  printf '%s' "$NEW" > "$FILE"
  chmod 600 "$FILE"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') refreshed from keychain" >> "$LOG"
fi
