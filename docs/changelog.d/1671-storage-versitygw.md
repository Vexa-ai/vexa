- **Old recordings stay in the old MinIO volume and do not play back from the new storage until copied with `make migrate-storage` (#1671).**
  Lite and Compose now store new recordings in versitygw (Apache-2.0, pinned by digest), in
  `vexa-lite-storagedata` and `<project>_storage-data` respectively. Startup does not migrate or
  inspect old volumes. The opt-in copy verifies objects by SHA-256, preserves and reports target
  edits/deletions on reruns, and never deletes the old volume. Compose checks its configured
  recording endpoint with a bucket check and a PUT/GET/DELETE probe before meeting-api starts;
  update an old `.env` endpoint to `MINIO_ENDPOINT=storage:9000` when switching. Compose
  `make down` keeps data volumes; `make destroy DESTROY=yes` explicitly deletes the current
  stack's volumes. See [Where your recordings live now](/upgrade-from-minio).
