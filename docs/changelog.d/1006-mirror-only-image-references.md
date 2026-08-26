- **Helm: a private-registry mirror can now hold every image the chart pulls (#1006).** Two of the
  runtime's spawned-workload references — `runtime.agentImage` and `runtime.agentWorkerImage` — were
  silently discarded whenever `global.imageTag` was set, rendering Docker Hub references a
  mirror-only cluster cannot pull; the `minio-init` hook Job hardcoded `minio/mc:latest`. The chart
  now resolves every image by one documented rule: an explicit full reference wins and
  `global.imageTag` never rewrites it, otherwise `repository@digest`, otherwise `repository:tag`.
  Ambiguous identities (a full reference *and* a digest) and malformed digests fail the render
  instead of silently falling back to a mutable tag. The MinIO client image is configurable
  (`minio.mc.reference` / `.digest` / `.image.repository` + `.image.tag`) and its default is the pinned RELEASE
  tag `latest` resolved to, so the bytes an unconfigured install pulls are unchanged. With stock
  values the render is otherwise byte-identical to v0.12.18. New `deploy/helm/tests/test_mirror_only.sh` renders a mirror-only fixture and fails if
  any container, hook or runtime-spawn reference falls outside the configured registry.
