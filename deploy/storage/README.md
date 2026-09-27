# deploy/storage — object-storage tooling for Lite and Compose

Lite and Compose store recordings in **versitygw** (`versity/versitygw`, Apache-2.0), an S3 server
whose POSIX backend keeps every object as a plain file in a local volume. meeting-api and the bots
keep speaking S3 with the same `MINIO_*` / `BOT_S3_*` variables; nothing in the application changed.
These scripts are run with the Python and boto3 already inside the meeting-api and Lite images, so
they add no image.

| File | Used by | What it does |
|---|---|---|
| `storage_init.py` | Compose `storage-init` | Creates the bucket; when authenticated bots point `BOT_S3_ENDPOINT` at the in-stack storage, creates their scoped account and a bucket policy limited to `BOT_USERDATA_S3_PATH`, then proves the limit. Exits 1 when `.env` still points `MINIO_ENDPOINT` at an old `minio` service that no longer answers. |
| `s3_copy.py` | both, via `migrate-from-minio.sh` | Verifying, resumable S3 → S3 copy. Uploads are read back and compared by SHA-256; the manifest records source and target size/ETag. Reruns preserve target edits and deletions, reporting them in `summary.json`. Identical untracked target objects are adopted without uploading. Deletes nothing. |
| `migrate-from-minio.sh` | `make -C deploy/lite migrate-storage`, `make -C deploy/compose migrate-storage` | Finds a MinIO that can read the old volume (the running container, else a temporary one from that container's image, else `LEGACY_MINIO_IMAGE` if present locally; never pulls), runs `s3_copy.py`, removes only the temporary containers it started. |

## Layout of the storage volume

```
/srv/vexa-storage/                 the volume (Lite: vexa-lite-storagedata, Compose: <project>_storage-data)
  data/<bucket>/<key>              every object, as a plain file (buckets are directories)
  iam/users.json                   accounts versitygw created (the scoped bot account, if any)
  migration-from-minio/            manifest.jsonl, summary.json, COMPLETE (or SKIPPED) after an upgrade
```

Content type and ETag live in extended attributes (`user.*` xattrs) on each file. Back the volume
up with an attribute-preserving copy (`rsync -aX`, `cp -a`); a plain copy keeps the bytes but loses
that metadata. One S3 rule follows from files: a key and a "folder" of the same name
(`a/b` and `a/b/c`) cannot both exist. Vexa's keys never do that.

Operator guide: [docs/docs/upgrade-from-minio.mdx](../../docs/docs/upgrade-from-minio.mdx).

Copy tests: [tests/README.md](tests/README.md). The summary reports
`changed_in_target_after_copy`, `deleted_in_target_after_copy`, `changed_on_both_sides`, and
`deleted_at_source_after_copy` (up to 1000 keys each; stdout gives each full count).
These reports do not fail the run: completion requires every source key to be verified against
its current source state or explicitly reported, with no verification failures. `--reverify`
hashes only previously verified target objects whose size and ETag still match the manifest.
