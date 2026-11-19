- **Helm: the runtime runs as uid 10001 on the Kubernetes backend.** On the default
  `runtime.backend: k8s` the runtime only calls the API server with its ServiceAccount, so its pod
  now sets `runAsNonRoot` with uid 10001 instead of running as root. The docker backend keeps root to
  hold the engine's socket, and the OpenShift profile still leaves every context to the SCC. See
  [What Vexa requires of your platform](/deployment-kubernetes#what-vexa-requires-of-your-platform) (K6).
