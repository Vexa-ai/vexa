- **Helm: recordings go to your own S3; the built-in MinIO is removed (#1671).** The chart's default
  MinIO could no longer be pulled, so fresh installs failed. Set `storage.s3.endpoint`,
  `storage.s3.bucket` and `storage.s3.existingSecret` (a Secret holding the key pair); region,
  path-style and a private CA are optional. The chart refuses to render without them, and refuses a
  leftover `minio.enabled: true`. Upgrading a release that ran the built-in MinIO: copy its objects
  to your bucket first — the upgrade leaves the MinIO PVC in place. See
  [Kubernetes → Upgrading from the built-in MinIO](/deployment-kubernetes#upgrading-from-the-built-in-minio).
