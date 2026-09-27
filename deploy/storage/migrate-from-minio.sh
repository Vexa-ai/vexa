#!/usr/bin/env bash
# Copy recordings from a MinIO-era install into the new storage (versitygw), verifying every object.
# Called by `make -C deploy/lite migrate-storage` and `make -C deploy/compose migrate-storage`, which
# set the variables below. It never deletes or rewrites anything: the old volume keeps every object,
# and a re-run copies only what an earlier run has not yet verified.
#
# MinIO keeps its data in its own on-disk format, not as plain files, so reading the old volume needs
# a running MinIO. In order of preference this script uses: the install's MinIO container if it is
# still running; else a temporary MinIO started on the old volume from the image of that container
# (a stopped container keeps its image on the machine); else LEGACY_IMAGE, which the operator names
# and which must already be on this machine (nothing is pulled). When none exists it stops, having
# changed nothing, and says how to continue.
set -uo pipefail

say() { echo "[migrate] $*"; }
for v in NETWORK LEGACY_VOLUME LEGACY_CONTAINER LEGACY_ACCESS_KEY LEGACY_SECRET_KEY LEGACY_BUCKET \
         TARGET_ENDPOINT TARGET_ACCESS_KEY TARGET_SECRET_KEY TARGET_BUCKET STORAGE_VOLUME \
         RUN_IMAGE RUN_PYTHON TOOLS_DIR NAME_PREFIX RETRY_HINT; do
  [ -n "${!v:-}" ] || { say "internal error: $v is not set"; exit 2; }
done
LEGACY_IMAGE="${LEGACY_IMAGE:-}"
DOC="https://docs.vexa.ai/upgrade-from-minio"

if ! docker volume inspect "$LEGACY_VOLUME" >/dev/null 2>&1; then
  say "No MinIO volume '$LEGACY_VOLUME' on this machine: nothing to migrate."
  exit 0
fi

tmp="" connected=""
cleanup() {
  [ -n "$tmp" ] && docker rm -f "$tmp" >/dev/null 2>&1
  [ -n "$connected" ] && docker network disconnect "$NETWORK" "$LEGACY_CONTAINER" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

running=$(docker inspect -f '{{.State.Running}}' "$LEGACY_CONTAINER" 2>/dev/null || echo absent)
if [ "$running" = "true" ]; then
  src_host="$LEGACY_CONTAINER"
  if ! docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$LEGACY_CONTAINER" | tr ' ' '\n' | grep -qx "$NETWORK"; then
    docker network connect "$NETWORK" "$LEGACY_CONTAINER" && connected=1
  fi
  say "reading from the running MinIO container '$LEGACY_CONTAINER' (volume '$LEGACY_VOLUME')"
else
  img="$LEGACY_IMAGE"
  if [ "$running" = "false" ] && [ -z "$img" ]; then
    img=$(docker inspect -f '{{.Config.Image}}' "$LEGACY_CONTAINER")
    say "MinIO container '$LEGACY_CONTAINER' exists but is stopped; reading its volume with its own image '$img'"
  fi
  if [ -z "$img" ] || ! docker image inspect "$img" >/dev/null 2>&1; then
    say "STOP: cannot start a MinIO to read volume '$LEGACY_VOLUME'."
    if [ -z "$img" ]; then
      say "  Its container '$LEGACY_CONTAINER' is gone, and no MinIO image was named (LEGACY_MINIO_IMAGE)."
    else
      say "  The image '$img' is not on this machine, and this script never pulls one"
      say "  (MinIO no longer publishes its community images)."
    fi
    say "  Your recordings are still in volume '$LEGACY_VOLUME'. Nothing was copied, changed or deleted."
    say "  If 'docker image ls' still lists the MinIO image this install used, name it and run again:"
    say "      $RETRY_HINT LEGACY_MINIO_IMAGE=<image>"
    say "  Otherwise you need a MinIO server image you trust that can open this volume. Guide: $DOC"
    exit 3
  fi
  tmp="${NAME_PREFIX}-minio-migrate"
  docker rm -f "$tmp" >/dev/null 2>&1
  if ! docker run -d --name "$tmp" --network "$NETWORK" \
      -e MINIO_ROOT_USER="$LEGACY_ACCESS_KEY" -e MINIO_ROOT_PASSWORD="$LEGACY_SECRET_KEY" \
      -v "$LEGACY_VOLUME:/data" "$img" server /data >/dev/null; then
    tmp=""
    say "STOP: the image '$img' did not start a MinIO server on volume '$LEGACY_VOLUME'. Nothing was copied, changed or deleted."
    say "  Set LEGACY_MINIO_IMAGE to a MinIO server image that can open this volume and run again. Guide: $DOC"
    exit 3
  fi
  src_host="$tmp"
  say "started a temporary MinIO '$tmp' ($img) on volume '$LEGACY_VOLUME'; it is removed when the copy ends"
fi

export SRC_ENDPOINT="http://${src_host}:9000" SRC_ACCESS_KEY="$LEGACY_ACCESS_KEY" SRC_SECRET_KEY="$LEGACY_SECRET_KEY" SRC_BUCKET="$LEGACY_BUCKET"
export DST_ENDPOINT="$TARGET_ENDPOINT" DST_ACCESS_KEY="$TARGET_ACCESS_KEY" DST_SECRET_KEY="$TARGET_SECRET_KEY" DST_BUCKET="$TARGET_BUCKET"
export STATE_DIR=/srv/vexa-storage/migration-from-minio
docker rm -f "${NAME_PREFIX}-storage-copy" >/dev/null 2>&1
docker run --rm --name "${NAME_PREFIX}-storage-copy" --network "$NETWORK" \
  -e SRC_ENDPOINT -e SRC_ACCESS_KEY -e SRC_SECRET_KEY -e SRC_BUCKET \
  -e DST_ENDPOINT -e DST_ACCESS_KEY -e DST_SECRET_KEY -e DST_BUCKET -e STATE_DIR \
  -v "$TOOLS_DIR:/vexa-storage-tools:ro" -v "$STORAGE_VOLUME:/srv/vexa-storage" \
  --entrypoint "$RUN_PYTHON" "$RUN_IMAGE" /vexa-storage-tools/s3_copy.py ${COPY_ARGS:-}
rc=$?
case $rc in
  0) say "Done. The old volume '$LEGACY_VOLUME' was only read; keep it until you have played recordings back."
     say "  Report: volume '$STORAGE_VOLUME', migration-from-minio/summary.json" ;;
  3) say "STOP: the MinIO on volume '$LEGACY_VOLUME' did not answer with the credentials and bucket given"
     say "  (LEGACY_MINIO_ACCESS_KEY / LEGACY_MINIO_SECRET_KEY / LEGACY_MINIO_BUCKET). Nothing was copied. Guide: $DOC" ;;
  *) say "The copy did not verify every object (exit $rc). Nothing was deleted; run again to retry: $RETRY_HINT" ;;
esac
exit $rc
