#!/bin/sh
# Install (or remove) the Claude credential sync for this machine.
#
#   ./install.sh            install / update
#   ./install.sh uninstall  remove
#
# macOS: registers a launchd user agent (label ai.vexa.claude-creds-sync) that
#   runs sync.sh every 5 minutes and at login, so the mounted credentials file
#   self-heals after every CLI reauth / token rotation.
# Linux & Windows(WSL2): no daemon is needed — the CLI refreshes the file
#   itself. The installer just sanity-checks that the file exists.
set -eu

LABEL="ai.vexa.claude-creds-sync"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SYNC="$SCRIPT_DIR/sync.sh"
FILE="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"

os=$(uname -s)

if [ "${1:-}" = "uninstall" ]; then
  if [ "$os" = "Darwin" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
    echo "removed $LABEL"
  else
    echo "nothing installed on $os"
  fi
  exit 0
fi

case "$os" in
  Darwin)
    if ! security find-generic-password -s "Claude Code-credentials" -w >/dev/null 2>&1; then
      echo "ERROR: no 'Claude Code-credentials' item in the login Keychain." >&2
      echo "Sign in first: run 'claude' and authenticate, then re-run this installer." >&2
      exit 1
    fi
    chmod +x "$SYNC"
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>$SYNC</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.claude/creds-sync.err</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    launchctl kickstart "gui/$(id -u)/$LABEL"
    echo "installed $LABEL (every 5 min + at login); log: ~/.claude/creds-sync.log"
    ;;
  Linux)
    # Covers native Linux and Windows via WSL2 — the CLI owns the file.
    if [ -f "$FILE" ]; then
      chmod 600 "$FILE" 2>/dev/null || true
      echo "no sync needed on Linux/WSL: $FILE is Claude Code's own store (refreshed by the CLI)."
    else
      echo "WARNING: $FILE not found. Sign in first: run 'claude' and authenticate." >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: unsupported OS '$os'. On Windows, run the stack and Claude Code inside WSL2." >&2
    exit 1
    ;;
esac
