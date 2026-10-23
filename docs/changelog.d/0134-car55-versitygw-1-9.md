- **Storage runs versitygw v1.9.0.** Lite and Compose's `storage` sidecar moves from v1.8.0 to v1.9.0,
  pinned by digest. It clears six of the seven HIGH findings Trivy reported in v1.8.0 (Alpine
  OpenSSL, `golang.org/x/net`, Go's standard library), and upstream fixes path traversal through
  multipart upload IDs and `DeleteObjects` keys. An existing volume is read as it is: `make up` or
  `docker compose up -d` replaces the container and keeps every recording. Lite's `STORAGE_IMAGE`
  overrides it as before.
