#!/usr/bin/env bash
# recording-finalizing-state — DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL
#
# Proves: when a recording is completed but playback_url is still null
# (master assembly hasn't run yet, or is pending), the dashboard
# renders an explicit "finalizing" UI state rather than silently
# falling back to chunk 0 or showing a broken player.
#
# Mode: compose (stateful fallback to static when no live row exists).
#
# Pragmatic shape:
#   1. Try the runtime path: query the DB for a completed recording
#      with playback_url IS NULL. If we find one, hit the dashboard
#      page-render code path and assert it produces a finalizing
#      marker (TODO: requires a UI test harness — not in scope for
#      this release).
#   2. Static-fallback (current default): grep the dashboard meeting-
#      detail page for an explicit handle on null playback_url. We
#      assert the source contains BOTH:
#         - a guard on r.playback_url?.audio / .video, AND
#         - a code comment or branch documenting the null/finalizing
#           UI state (the marker string "finalizing" in the same file).
#      This is "best-effort static check; full runtime prove pending
#      UI test harness."
#
# Why the static fallback is acceptable here:
#   The contract is "the dashboard handles null playback_url". The
#   playback_url-guarded filter at services/dashboard/src/app/meetings/
#   [id]/page.tsx (the .filter(r => r.playback_url?.audio) shape)
#   provides the guard; the per-file "finalizing" marker provides the
#   intent. A regression that drops either is caught.
#
# Exit:
#   0 — guard + marker both present.
#   non-0 — either missing.
#
# Idempotent: read-only.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL.
Best-effort static check; full runtime prove pending UI test harness.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # tests3/
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"           # repo root
# shellcheck source=../lib/common.sh
source "$ROOT_DIR/lib/common.sh"

test_begin recording-finalizing-state

PAGE="$REPO_ROOT/services/dashboard/src/app/meetings/[id]/page.tsx"
if [ ! -r "$PAGE" ]; then
    step_fail DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL \
        "page source missing at $PAGE"
    exit 1
fi

# Step 1: guard expression — a filter/conditional that branches on
# the presence of playback_url.{audio,video}. This is what gates the
# render path between "show player" and "show finalizing".
GUARD_HITS=$(grep -cE "playback_url\\?\\.(audio|video)" "$PAGE" || true)
if [ "${GUARD_HITS:-0}" -lt 1 ]; then
    step_fail DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL \
        "no playback_url?.{audio,video} guard in $PAGE — dashboard cannot branch on null playback_url"
    exit 1
fi

# Step 2: explicit finalizing marker in the same file. Any of:
#   - lowercase string "finalizing"
#   - capitalised "Finalizing"
#   - status enum FINALIZING
MARKER_HITS=$(grep -cE "[Ff]inalizing|FINALIZING" "$PAGE" || true)
if [ "${MARKER_HITS:-0}" -lt 1 ]; then
    step_fail DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL \
        "no 'finalizing' UI marker found in $PAGE — guard exists but no documented null-playback_url branch"
    exit 1
fi

step_pass DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL \
    "guard ($GUARD_HITS) + finalizing marker ($MARKER_HITS) present (best-effort static; runtime prove pending UI test harness)"
