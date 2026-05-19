#!/usr/bin/env bash
# recording-playback-url-canonical — RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE
#
# Proves: every JSONB recording with status='completed' AND a master
# media_file entry (finalized_by == 'recording_finalizer.master') ALSO
# has a non-null playback_url.audio (and .video if a video master exists).
#
# Mode: compose. Requires the vexa-postgres-1 container running with a
# populated `meetings` table.
#
# Why this matters:
#   v0.10.6.1 makes recording.playback_url the canonical pointer the
#   dashboard reads. A completed recording with a master but no
#   playback_url renders forever as "finalizing". The m314 backfill +
#   the post-finalize write path must both keep this invariant.
#
# Exit:
#   0 — every completed-with-master recording has playback_url; no
#       silent fallback if DB query fails.
#   non-0 — at least one recording violates the invariant, or the DB
#       was unreachable.
#
# Idempotent: read-only; safe to re-run.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0
Proves RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE.
Reads from vexa-postgres-1 (compose). No arguments.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT_DIR/lib/common.sh"

test_begin recording-playback-url-canonical

PG_CONTAINER="${PG_CONTAINER:-vexa-postgres-1}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-vexa}"

if ! command -v docker >/dev/null 2>&1; then
    step_fail RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE "docker not available — required to reach $PG_CONTAINER"
    exit 1
fi

if ! docker exec "$PG_CONTAINER" true >/dev/null 2>&1; then
    step_fail RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE "cannot exec into $PG_CONTAINER (is the compose stack up?)"
    exit 1
fi

# Query: enumerate completed recordings whose media_files contains an
# entry tagged as a master, AND playback_url is null/missing.
# A row in the output = a violation.
SQL=$(cat <<'PSQL'
WITH recs AS (
    SELECT
        m.id            AS meeting_id,
        rec->>'id'      AS rec_id,
        rec->>'status'  AS rec_status,
        rec->'playback_url' AS playback_url,
        rec->'media_files'  AS media_files
    FROM meetings m,
         jsonb_array_elements(m.data->'recordings') rec
    WHERE jsonb_typeof(m.data->'recordings') = 'array'
)
SELECT
    meeting_id,
    rec_id,
    (
        SELECT bool_or(mf->>'type' = 'audio'
                       AND mf->>'finalized_by' = 'recording_finalizer.master')
        FROM jsonb_array_elements(media_files) mf
    ) AS has_audio_master,
    (
        SELECT bool_or(mf->>'type' = 'video'
                       AND mf->>'finalized_by' = 'recording_finalizer.master')
        FROM jsonb_array_elements(media_files) mf
    ) AS has_video_master,
    playback_url
FROM recs
WHERE rec_status = 'completed'
  AND jsonb_typeof(media_files) = 'array'
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(media_files) mf
      WHERE mf->>'finalized_by' = 'recording_finalizer.master'
  )
  AND (
      playback_url IS NULL
      OR playback_url = 'null'::jsonb
      OR (
          (SELECT bool_or(mf->>'type' = 'audio'
                          AND mf->>'finalized_by' = 'recording_finalizer.master')
           FROM jsonb_array_elements(media_files) mf)
          AND (playback_url->>'audio') IS NULL
      )
      OR (
          (SELECT bool_or(mf->>'type' = 'video'
                          AND mf->>'finalized_by' = 'recording_finalizer.master')
           FROM jsonb_array_elements(media_files) mf)
          AND (playback_url->>'video') IS NULL
      )
  );
PSQL
)

OUT_FILE="$(mktemp -t playback-canon-XXXXXX.tsv)"
# Chain cleanup onto the report-flush trap installed by test_begin
# (common.sh). A plain `trap ... EXIT` would silently drop the JSON
# report.
trap '_flush_test_report; rm -f "$OUT_FILE"' EXIT INT TERM

if ! docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -A -F $'\t' -t -c "$SQL" \
        > "$OUT_FILE" 2>/dev/null; then
    step_fail RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE "psql query failed (see container logs)"
    exit 1
fi

# Strip blank lines that psql emits.
sed -i '/^$/d' "$OUT_FILE"

VIOLATIONS=$(wc -l < "$OUT_FILE" | tr -d ' ')

if [ "$VIOLATIONS" -gt 0 ]; then
    HEAD_SAMPLE=$(head -5 "$OUT_FILE" | tr '\n' '|')
    step_fail RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE \
        "$VIOLATIONS completed recording(s) with master but missing playback_url. sample: $HEAD_SAMPLE"
    exit 1
fi

step_pass RECORDING_HAS_PLAYBACK_URL_AFTER_FINALIZE \
    "all completed recordings with master have non-null playback_url"
