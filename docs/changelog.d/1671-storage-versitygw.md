- **Old recordings stay in the old MinIO volume and do not play back until copied with `make migrate-storage` (the API answers HTTP 500 until copied; #1671).**
  Lite and Compose now store new recordings in versitygw (Apache-2.0, pinned by digest), in
  `vexa-lite-storagedata` and `<project>_storage-data` respectively. Startup does not migrate or
  write old volumes itself. With a stale `MINIO_ENDPOINT=minio:9000` and a running old MinIO,
  meeting-api keeps writing new recordings to the old volume. The opt-in copy verifies objects
  by SHA-256, preserves and reports target edits/deletions on reruns, and never deletes the old
  volume. An ETag-only change with identical bytes updates the manifest without a false report.
  Compose checks its configured
  recording endpoint with a bucket check and a PUT/GET/DELETE probe before meeting-api starts;
  update an old `.env` endpoint to `MINIO_ENDPOINT=storage:9000` before starting and check that
  storage-init's `Ready: endpoint …` line names the new storage. Never remove the old MinIO
  container or its volume until the script's last run ends with **VERIFIED**; reruns also copy
  recordings written to the old MinIO after the upgrade. Compose
  `make down` keeps data volumes; `make destroy DESTROY=yes` explicitly deletes the current
  stack's volumes. See [Where your recordings live now](/upgrade-from-minio).
