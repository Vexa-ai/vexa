#!/bin/bash
# Hot-reload dev loop — run ON YOUR MAC (one paste):
#
#   ssh dima@192.168.1.4 'cat ~/dev/vexa/services/vexa-extension/dev/mac-sync.sh' | bash
#
# (or with your ssh alias:  ssh bbb 'cat ~/dev/vexa/...' | bash -s -- bbb )
#
# What it does:
#  - mirrors the server's extension dist/ to ~/vexa-ext-dist every 2s (rsync)
#  - runs in the background (nohup), survives closing the terminal
#  - the extension watches dist/build-stamp.txt and reloads itself on change
#
# One-time after first run: chrome://extensions → Load unpacked → ~/vexa-ext-dist
# After that: every rebuild on the server lands in Chrome automatically.

set -euo pipefail

HOST="${1:-dima@192.168.1.4}"
REMOTE_DIST="dev/vexa/services/vexa-extension/dist/"
LOCAL_DIST="$HOME/vexa-ext-dist"
PIDFILE="$HOME/.vexa-ext-sync.pid"
LOGFILE="$HOME/.vexa-ext-sync.log"

# Stop a previous loop if running
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  echo "stopped previous sync loop (pid $(cat "$PIDFILE"))"
fi

mkdir -p "$LOCAL_DIST"

# First sync now (fails loudly if ssh/host is wrong)
rsync -az --delete "$HOST:$REMOTE_DIST" "$LOCAL_DIST/"
echo "initial sync OK → $LOCAL_DIST"

# Background watch loop
nohup bash -c "while true; do rsync -az --delete '$HOST:$REMOTE_DIST' '$LOCAL_DIST/' 2>>'$LOGFILE'; sleep 2; done" >/dev/null 2>&1 &
echo $! > "$PIDFILE"

echo ""
echo "✅ sync loop running (pid $(cat "$PIDFILE"), log: $LOGFILE)"
echo "   one-time: chrome://extensions → Developer mode → Load unpacked → $LOCAL_DIST"
echo "   (remove any previously-loaded copy of the extension first)"
echo "   stop later with: kill \$(cat $PIDFILE)"
