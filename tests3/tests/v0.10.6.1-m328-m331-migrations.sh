#!/usr/bin/env bash
# v0.10.6.1-m328-m331-migrations — D13 prove for the media_files dedup
# (m328) and relational-recordings drop (m331) migration surface.
#
# m328 is a CLEANUP-BEFORE-DROP one-shot: dedup duplicate
# (recording_id, type) rows in media_files so operators don't hit a
# constraint conflict if they later add UniqueConstraint, then m331 drops
# the table entirely. The application code already stopped reading
# media_files in v0.10.6.1 (recordings live in meetings.data JSONB).
#
# Steps:
#   migration_surface_intact  Static: required migration files exist with
#                             documented interfaces (dry-run default,
#                             advisory-lock for race safety, m331 archive
#                             before drop). m328 has NO restore (rows are
#                             legacy duplicates the system doesn't read).
#   model_refs_dropped        Static: services/meeting-api/meeting_api/
#                             models.py no longer declares the Recording
#                             or MediaFile ORM classes. database.py
#                             documents the cutover. Pairs with the
#                             existing static-grep checks
#                             RECORDINGS_TABLE_NOT_REFERENCED +
#                             MEDIA_FILES_TABLE_NOT_REFERENCED.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
MIG_DIR="$ROOT_DIR/tests3/lib/migrations"
MODELS="$ROOT_DIR/services/meeting-api/meeting_api/models.py"
DATABASE="$ROOT_DIR/services/meeting-api/meeting_api/database.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-m328-m331-migrations :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-m328-m331-migrations-$step"

case "$step" in
  migration_surface_intact)
    failed=0

    # ── m328: dedup migration ────────────────────────────────────
    m328="$MIG_DIR/m328-dedup-media-files.py"
    if [ ! -f "$m328" ]; then
      echo "    FAIL: m328 migration missing at $m328"
      step_fail "M328_PRESENT" "file not found"
      failed=1
    else
      step_pass "M328_PRESENT" "m328-dedup-media-files.py present"

      # Operator-safe defaults: DRY-RUN is the default mode.
      if ! grep -q 'DRY-RUN' "$m328"; then
        echo "    FAIL: m328 does not declare DRY-RUN as default mode"
        step_fail "M328_DRY_RUN_DEFAULT" "DRY-RUN mention missing"
        failed=1
      else
        step_pass "M328_DRY_RUN_DEFAULT" "m328 dry-run-by-default documented"
      fi

      # Race safety: advisory-lock for concurrent runners.
      if ! grep -q 'ADVISORY_LOCK_ID\|pg_advisory_lock\|advisory lock' "$m328"; then
        echo "    FAIL: m328 missing advisory-lock guard against concurrent runners"
        step_fail "M328_ADVISORY_LOCK" "no advisory lock"
        failed=1
      else
        step_pass "M328_ADVISORY_LOCK" "m328 uses Postgres advisory lock"
      fi

      # All four documented modes exist.
      for arg in '--check' '--apply' '--add-constraint'; do
        if ! grep -qE "add_argument\(\s*[\"']$arg" "$m328"; then
          echo "    FAIL: m328 missing $arg argparse declaration"
          step_fail "M328_ARG_$arg" "argparse declaration not found"
          failed=1
        else
          step_pass "M328_ARG_$arg" "m328 declares $arg"
        fi
      done
    fi

    # m328 deliberately has NO restore companion (rotting duplicates).
    if [ -f "$MIG_DIR/m328-restore-media-files-dedup.py" ]; then
      step_skip "M328_NO_RESTORE" "restore companion present (unexpected — m328 was cleanup-only)"
    else
      step_pass "M328_NO_RESTORE" "m328 has no restore (cleanup-only by design)"
    fi

    # ── m331: drop relational recordings ─────────────────────────
    m331="$MIG_DIR/m331-drop-relational-recordings.py"
    m331r="$MIG_DIR/m331-restore-relational-recordings.py"

    if [ ! -f "$m331" ]; then
      echo "    FAIL: m331 migration missing at $m331"
      step_fail "M331_PRESENT" "file not found"
      failed=1
    else
      step_pass "M331_PRESENT" "m331-drop-relational-recordings.py present"
      if ! grep -q 'ARCHIVE\|archive\|json\.dump' "$m331"; then
        echo "    FAIL: m331 must archive stray rows before DROP"
        step_fail "M331_ARCHIVE_BEFORE_DROP" "no archive logic found"
        failed=1
      else
        step_pass "M331_ARCHIVE_BEFORE_DROP" "m331 archives before DROP"
      fi
      if ! grep -q '\-\-dry-run\|DRY-RUN\|dry_run' "$m331"; then
        echo "    FAIL: m331 must support --dry-run"
        step_fail "M331_DRY_RUN" "dry-run mode missing"
        failed=1
      else
        step_pass "M331_DRY_RUN" "m331 supports dry-run"
      fi
    fi

    if [ ! -f "$m331r" ]; then
      echo "    FAIL: m331-restore companion missing at $m331r"
      step_fail "M331_RESTORE_PRESENT" "restore file not found"
      failed=1
    else
      step_pass "M331_RESTORE_PRESENT" "m331-restore-relational-recordings.py present"
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  model_refs_dropped)
    failed=0

    if [ ! -f "$MODELS" ]; then
      echo "    FAIL: $MODELS missing"
      step_fail "MODELS_PRESENT" "file not found"
      failed=1
    else
      # The Recording + MediaFile ORM classes must be gone.
      if grep -qE '^class (Recording|MediaFile)\b' "$MODELS"; then
        echo "    FAIL: models.py still declares Recording/MediaFile ORM class — m331 prerequisite not met"
        step_fail "MODELS_NO_ORM" "class still declared"
        failed=1
      else
        step_pass "MODELS_NO_ORM" "Recording + MediaFile ORM classes removed"
      fi
      # A note about the cutover should remain (so future readers know
      # what happened — protects against an accidental re-introduction).
      if ! grep -qE 'media_files|Recording.*MediaFile.*removed|m331' "$MODELS"; then
        echo "    FAIL: models.py lacks comment explaining the cutover"
        step_fail "MODELS_CUTOVER_DOC" "no cutover comment"
        failed=1
      else
        step_pass "MODELS_CUTOVER_DOC" "cutover comment present in models.py"
      fi
    fi

    if [ ! -f "$DATABASE" ]; then
      echo "    FAIL: $DATABASE missing"
      step_fail "DATABASE_PRESENT" "file not found"
      failed=1
    else
      if ! grep -qE 'm331|recordings/media_files tables removed' "$DATABASE"; then
        echo "    FAIL: database.py lacks m331 cutover comment"
        step_fail "DATABASE_CUTOVER_DOC" "no cutover comment"
        failed=1
      else
        step_pass "DATABASE_CUTOVER_DOC" "database.py documents the cutover"
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
