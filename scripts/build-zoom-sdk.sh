#!/usr/bin/env bash
# build-zoom-sdk.sh — build the native Zoom Meeting SDK wrapper addon.
#
# Pack B (release 260422-zoom-sdk, #150 P1 §3): single-command build for
# self-hosters who want platform=zoom_sdk. Wraps `npx node-gyp rebuild`
# with the prerequisite checks + system-deps install + post-build smoke.
#
# Usage (from repo root):
#   bash scripts/build-zoom-sdk.sh
#
# Exits non-zero with an actionable message for each failure mode.
# Full setup guide: services/vexa-bot/docs/zoom-sdk-setup.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="$REPO_ROOT/services/vexa-bot/core/src/platforms/zoom-sdk/native/zoom_meeting_sdk"
BOT_DIR="$REPO_ROOT/services/vexa-bot"

color() { if [ -t 1 ]; then printf "\033[%sm%s\033[0m" "$1" "$2"; else printf "%s" "$2"; fi; }
step() { printf "\n%s %s\n" "$(color '1;36' '▶')" "$1"; }
ok()   { printf "%s %s\n" "$(color '1;32' '✓')" "$1"; }
fail() { printf "%s %s\n" "$(color '1;31' '✗')" "$1" >&2; exit 1; }

# ── Step 1: SDK binaries present? ────────────────────────────────────
step "Step 1/5: check Zoom SDK binaries"
if [ ! -f "$SDK_DIR/libmeetingsdk.so" ]; then
  fail "libmeetingsdk.so missing at:
    $SDK_DIR/libmeetingsdk.so

  Download the Zoom Meeting SDK (Linux x86_64) from
  marketplace.zoom.us -> your Meeting-SDK app -> Download.
  Extract and place these files under $SDK_DIR/ :
    - libmeetingsdk.so  (+ symlink libmeetingsdk.so.1)
    - libcml.so
    - libmpg123.so
    - qt_libs/Qt/lib/   (nested directory with bundled Qt libraries)

  Full guide: services/vexa-bot/docs/zoom-sdk-setup.md"
fi
if [ ! -d "$SDK_DIR/qt_libs" ]; then
  fail "qt_libs/ missing at:
    $SDK_DIR/qt_libs

  The Zoom SDK ships bundled Qt libraries that must load BEFORE
  system Qt (#150 P0 §2). Place the qt_libs/ tree as extracted
  from the SDK archive."
fi
ok "SDK files present"

# ── Step 2: system deps (apt-get) ─────────────────────────────────────
step "Step 2/5: install system build dependencies"
if command -v apt-get >/dev/null 2>&1; then
  NEEDED_PKGS="qtbase5-dev libxcb-xtest0"
  MISSING=""
  for pkg in $NEEDED_PKGS; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
  done
  if [ -n "$MISSING" ]; then
    echo "  Missing packages:$MISSING"
    if [ "$(id -u)" = 0 ]; then
      apt-get update && apt-get install -y $MISSING
    elif command -v sudo >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y $MISSING
    else
      fail "Missing:$MISSING. Install with: apt-get install -y $MISSING"
    fi
  fi
  ok "System dependencies installed"
else
  echo "  (skipping apt-get — not a Debian/Ubuntu host; ensure qtbase5-dev + libxcb-xtest0 equivalents are installed)"
fi

# ── Step 3: npm install (no postinstall native build) ────────────────
step "Step 3/5: npm install (workspace, no native postinstall)"
cd "$BOT_DIR"
npm install --ignore-scripts >/dev/null
ok "npm install complete"

# ── Step 4: node-gyp rebuild ──────────────────────────────────────────
step "Step 4/5: node-gyp rebuild (native addon)"
npx node-gyp rebuild 2>&1 | tail -40 || fail "node-gyp rebuild failed — see output above"
ok "Native addon built"

# ── Step 5: smoke — require() the addon ───────────────────────────────
step "Step 5/5: smoke-load the built addon"
cd "$BOT_DIR"
export LD_LIBRARY_PATH="$SDK_DIR/qt_libs/Qt/lib:$SDK_DIR:${LD_LIBRARY_PATH:-}"
if node -e "require('./build/Release/zoom_sdk_wrapper'); console.log('[smoke] addon loaded successfully')" 2>/tmp/zoom-sdk-smoke.log; then
  ok "Addon loads cleanly"
else
  echo ""
  echo "--- smoke output ---"
  cat /tmp/zoom-sdk-smoke.log >&2
  fail "Addon failed to load. Common causes:
   - LD_LIBRARY_PATH missing qt_libs/Qt/lib prefix (see entrypoint.sh)
   - libxcb-xtest0 not installed at runtime
   - SDK version mismatch (this wrapper was tested against SDK 6.7.2.7020)
   - Marketplace-SDK-app not published (code 63: external-meeting join blocked)
  Full guide: services/vexa-bot/docs/zoom-sdk-setup.md"
fi

echo ""
ok "Build complete. Native addon at: $BOT_DIR/build/Release/zoom_sdk_wrapper.node"
echo ""
echo "Next steps:"
echo "  1. Set ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET in .env (from your Marketplace app)."
echo "  2. POST /bots with platform=\"zoom_sdk\" + native_meeting_id + passcode."
echo "  3. First bot creation confirms the path end-to-end. If NO_PERMISSION:"
echo "     enable Recording -> 'Record to computer files' + auto-approve on the host Zoom account."
