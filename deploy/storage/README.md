# deploy/storage — object-storage tooling for Lite and Compose

Lite and Compose store recordings in **versitygw** (`versity/versitygw`, Apache-2.0), an S3 server
whose POSIX backend keeps every object as a plain file in a local volume. meeting-api and the bots
keep speaking S3 with the same `MINIO_*` / `BOT_S3_*` variables; nothing in the application changed.
These scripts are run with the Python and boto3 already inside the meeting-api and Lite images, so
they add no image.

| File | Used by | What it does |
|---|---|---|
| `storage_init.py` | Compose `storage-init` | Resolves meeting-api's configured endpoint, creates the bucket if absent, then PUTs/GETs/DELETEs a unique probe under `.vexa-storage-init/` and compares bytes. Any failure exits 1, naming endpoint, bucket and step. After readiness, emits a WARNING on every run when the recording endpoint's host or port differs from bundled storage, surfaced by `make up` and `make dev`. When bots point `BOT_S3_ENDPOINT` at in-stack storage, also provisions their scoped account with a **read-only** prefix policy (`s3:ListBucket` + `s3:GetObject` on `BOT_USERDATA_S3_PATH`, no Put/Delete) and proves it — own prefix listable and readable, writing or deleting there denied, recordings and the rest denied — without writing to recordings. Refuses a bot pair that shares either half with `MINIO_*` or `S3_*`. |
| `s3_copy.py` | both, via `migrate-from-minio.sh` | Verifying, resumable S3 → S3 copy. Uploads are read back and compared by SHA-256; the manifest records source and target size/ETag. Reruns preserve target edits and deletions, reporting them in `summary.json`. Identical untracked target objects are adopted without uploading. Deletes nothing. |
| `migrate-from-minio.sh` | `make -C deploy/lite migrate-storage`, `make -C deploy/compose migrate-storage` | Finds a MinIO that can read the old volume (the running container, else a temporary one from that container's image, else `LEGACY_MINIO_IMAGE` if present locally; never pulls), runs `s3_copy.py`, removes only the temporary containers it started. |

## Layout of the storage volume

```
/srv/vexa-storage/                 the volume (Lite: vexa-lite-storagedata, Compose: <project>_storage-data)
  data/<bucket>/<key>              every object, as a plain file (buckets are directories)
  iam/users.json                   accounts versitygw created (the scoped bot account, if any)
  migration-from-minio/            manifest.jsonl, summary.json after an opt-in copy
```

Content type and ETag live in extended attributes (`user.*` xattrs) on each file. Back the volume
up with an attribute-preserving copy (`rsync -aX`, `cp -a`); a plain copy keeps the bytes but loses
that metadata. One S3 rule follows from files: a key and a "folder" of the same name
(`a/b` and `a/b/c`) cannot both exist. Vexa's keys never do that.

Startup does not inspect migration receipts or write the old volume itself. If Compose's `.env`
still has `MINIO_ENDPOINT=minio:9000` and the old MinIO is running, meeting-api keeps writing new
recordings there. Change `.env` first; `make up` then prints no storage line for the bundled store,
a WARNING line naming any other store, or the STOP line if the store did not answer.
Old recordings stay in the MinIO volume and do not play back until copied
to the new storage (the API answers HTTP 500). Operator guide:
[Where your recordings live now](../../docs/docs/upgrade-from-minio.mdx).

Never remove the old MinIO container or its volume until the script's last run ends with
**VERIFIED**. The script also copies recordings written to the old MinIO after the upgrade;
switch endpoints and rerun it before retiring MinIO. Review reported conflicts and check playback.

Copy tests: [tests/README.md](tests/README.md). The summary reports
`changed_in_target_after_copy`, `deleted_in_target_after_copy`, `changed_on_both_sides`, and
`deleted_at_source_after_copy` (up to 1000 keys each; stdout gives each full count).
These reports do not fail the run: completion requires every source key to be verified against
its current source state or explicitly reported, with no verification failures. `--reverify`
hashes previously verified target objects whose size and ETag still match the manifest.
When only a verified target's ETag differs, the script hashes it even without `--reverify`:
identical bytes update the manifest's ETag; different bytes are reported as changed.
