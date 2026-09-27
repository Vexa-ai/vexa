- **Lite and Compose store recordings in versitygw instead of MinIO (#1671).** MinIO's community
  images can no longer be pulled, so fresh Lite and Compose installs failed before Vexa started.
  Both now run versitygw (Apache-2.0, pinned by digest), which keeps every recording as a plain
  file in a local volume: `vexa-lite-storagedata` on Lite, `storage-data` on Compose. The API and
  meeting-api's `MINIO_*` settings are unchanged. Installs that ran MinIO copy their recordings
  with `make migrate-storage`, which verifies copies by SHA-256, reports and preserves target edits/deletions on reruns, and never deletes the old
  volume; Lite refuses to start on the new storage until that copy is done or skipped. See
  [Upgrade from MinIO](/upgrade-from-minio). The Helm chart still runs MinIO and is unchanged.
