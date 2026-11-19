- **The object storage's root credentials have no published default (#1784).** Compose and Lite used
  to start their bundled storage with `vexa-access-key` / `vexa-secret-key`. Both now refuse an unset
  or published pair: `make up` (Compose, via `mint-dev-env.sh`) and `make lite` / `make -C deploy/lite
  up` mint a pair into `.env`, replace a published one, and restart the storage with it over the same
  volume, so existing recordings stay readable with no separate step. The Lite image refuses to start
  with `MINIO_ENDPOINT` set on an unset or published pair. **If you start Compose with `docker compose`
  directly, run `deploy/compose/mint-dev-env.sh .env` first.** Migrating from an older MinIO keeps
  working: the old pair is kept as `LEGACY_MINIO_*`. See [Configuration](/configuration#object-storage-recordings).
