#!/usr/bin/env bash
# v0.10.6.1-dashboard-playback-canonical — pins the ADR-2 dashboard
# playback-URL canonicalization contract: the meetings/[id] page reads
# recording.playback_url.{audio,video} as the single source of truth and
# never falls back to client-side master-picking over media_files[].
#
# Steps:
#   reads_playback_url           Static (services/dashboard/src/app/
#                                meetings/[id]/page.tsx): the page
#                                references playback_url.audio and
#                                playback_url.video; calls
#                                vexaAPI.getRecordingMasterStreamUrl
#                                to resolve each.
#   no_pick_master_media_file    Static: pickMasterMediaFile is not
#                                imported or referenced. media_files[]
#                                is only mentioned in comments/types,
#                                never used as the playback source.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
PAGE="$ROOT_DIR/services/dashboard/src/app/meetings/[id]/page.tsx"
API_LIB="$ROOT_DIR/services/dashboard/src/lib/api.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-dashboard-playback-canonical :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-dashboard-playback-canonical-$step"

case "$step" in
  reads_playback_url)
    failed=0

    if [ ! -f "$PAGE" ]; then
      echo "    FAIL: meetings/[id]/page.tsx missing"
      step_fail "PAGE_PRESENT" "file not found"
      failed=1
    else
      step_pass "PAGE_PRESENT" "meetings/[id]/page.tsx present"

      # Both audio + video reads.
      for kind in audio video; do
        if ! grep -qE "playback_url\??\.$kind" "$PAGE"; then
          echo "    FAIL: page does not read playback_url.$kind"
          step_fail "READS_PLAYBACK_${kind^^}" "playback_url.$kind not referenced"
          failed=1
        else
          step_pass "READS_PLAYBACK_${kind^^}" "page reads playback_url.$kind"
        fi
      done

      # Calls the resolver helper.
      if ! grep -qE 'getRecordingMasterStreamUrl\(' "$PAGE"; then
        echo "    FAIL: page does not call getRecordingMasterStreamUrl"
        step_fail "CALLS_STREAM_RESOLVER" "resolver call missing"
        failed=1
      else
        step_pass "CALLS_STREAM_RESOLVER" "page calls getRecordingMasterStreamUrl"
      fi
    fi

    if [ ! -f "$API_LIB" ]; then
      echo "    FAIL: dashboard lib/api.ts missing"
      step_fail "API_LIB_PRESENT" "file not found"
      failed=1
    else
      if ! grep -qE 'async\s+getRecordingMasterStreamUrl' "$API_LIB"; then
        echo "    FAIL: lib/api.ts does not export getRecordingMasterStreamUrl"
        step_fail "RESOLVER_EXPORTED" "method declaration missing"
        failed=1
      else
        step_pass "RESOLVER_EXPORTED" "lib/api.ts exports getRecordingMasterStreamUrl"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  no_pick_master_media_file)
    failed=0

    if [ ! -f "$PAGE" ]; then
      echo "    FAIL: page missing"
      step_fail "PAGE_PRESENT" "file not found"
      failed=1
    else
      # The legacy client-side master picker is gone.
      if grep -q 'pickMasterMediaFile' "$PAGE"; then
        echo "    FAIL: page still references pickMasterMediaFile (legacy client-side picker)"
        step_fail "NO_LEGACY_PICKER" "pickMasterMediaFile present"
        failed=1
      else
        step_pass "NO_LEGACY_PICKER" "pickMasterMediaFile no longer referenced"
      fi

      # media_files is not actively read as a playback source.
      # (We allow comments + type imports — but not subscript access
      # like media_files[ or property access like .media_files outside
      # of comment lines.)
      offending="$(grep -nE '\.media_files\[|media_files\.map|media_files\.find|media_files\.filter' "$PAGE" | grep -vE '^\s*[0-9]+:\s*//' || true)"
      if [ -n "$offending" ]; then
        echo "    FAIL: page actively reads media_files[] as a playback source:"
        echo "$offending" | sed 's/^/      /'
        step_fail "NO_MEDIA_FILES_READ" "active media_files iteration found"
        failed=1
      else
        step_pass "NO_MEDIA_FILES_READ" "media_files not iterated as playback source"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  *)
    echo "    FAIL: unknown step '$step'"
    test_end
    exit 1
    ;;
esac

test_end
echo ""
echo "  PASS"
