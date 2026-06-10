#!/bin/bash
# DURABLE hot-reload sync — run ONCE on your Mac:
#
#   ssh bbb 'cat ~/dev/vexa/services/vexa-extension/dev/mac-sync-launchd.sh' | bash -s -- bbb
#
# Installs a launchd agent that rsyncs the server's extension dist/ → ~/vexa-ext-dist
# every 3s via launchd (StartInterval), NOT a shell loop — so it survives sleep,
# wake, network drops, and reboot. The extension's build-stamp watcher then
# reloads it automatically. Kills the old fragile loop if present.

set -euo pipefail
HOST="${1:-dima@192.168.1.4}"
REMOTE_DIST="dev/vexa/services/vexa-extension/dist/"
LOCAL_DIST="$HOME/vexa-ext-dist"
LABEL="ai.vexa.extsync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNNER="$HOME/.vexa-ext-sync-run.sh"

# Stop the old fragile loop, if any
[[ -f "$HOME/.vexa-ext-sync.pid" ]] && kill "$(cat "$HOME/.vexa-ext-sync.pid")" 2>/dev/null || true

mkdir -p "$LOCAL_DIST" "$HOME/Library/LaunchAgents"

cat > "$RUNNER" <<RUN
#!/bin/bash
exec rsync -az --delete -e ssh "$HOST:$REMOTE_DIST" "$LOCAL_DIST/"
RUN
chmod +x "$RUNNER"

# First sync now (fails loudly if ssh/host wrong)
"$RUNNER" && echo "initial sync OK → $LOCAL_DIST"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$RUNNER</string></array>
  <key>StartInterval</key><integer>3</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.vexa-ext-sync.log</string>
</dict></plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo ""
echo "✅ launchd agent '$LABEL' installed — syncs every 3s, survives sleep/reboot."
echo "   one-time: chrome://extensions → Load unpacked → $LOCAL_DIST (remove old copy first)"
echo "   stop later: launchctl unload $PLIST"
