- **Service images run as a non-root user (#1784).** The gateway, admin-api, meeting-api, MCP,
  terminal and flows images run as uid 10001. On Compose the `identity-keys` one-shot hands the
  gateway's signing key to that uid on every start, including a key an older stack wrote. On
  Kubernetes those pods set `runAsNonRoot`, uid and gid 10001 and `fsGroup` 10001; values in
  `global.podSecurityContext` still win. The runtime and agent-api keep running as root, and Lite is
  unchanged. See [Kubernetes](/deployment-kubernetes).
