- **Storage runs versitygw v1.10.0; the search sidecar pins SearXNG 2026.10.24.** Lite and Compose's
  `storage` sidecar (and the Helm test S3 fixture) move from v1.9.0 to v1.10.0, pinned by digest:
  it is built with Go 1.27.3, which fixes CVE-2026-111943 (crypto/x509), the one HIGH finding left
  in v1.9.0. An existing volume is read as it is. Compose's opt-in `searxng` sidecar moves to
  `2026.10.24-ef0ec03da`, which carries the fixed `certifi` (GHSA-6q48-6c7w-9gw2). Both scan with
  no fixed CRITICAL or HIGH finding (Trivy 0.74.0). `STORAGE_IMAGE` and `SEARXNG_IMAGE` override
  them as before.
