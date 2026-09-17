- **Deploy: MinIO sidecar images come from quay.io, pinned to a dated release (#1671).** MinIO
  withdrew its Docker Hub repository, so `minio/minio:latest` and `minio/mc:latest` fail with
  "pull access denied" on any machine without a local cache — `make all` (compose), `make lite`
  and the Helm chart all stopped at the object store. Compose, Lite and the chart now default to
  `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` and `quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z`;
  override with `MINIO_IMAGE` / `MINIO_MC_IMAGE` (compose `.env`, Lite `make` variables) or
  `minio.image` / `minio.mcImage` (Helm values) to use a mirror. See [Deployment](/deployment).
