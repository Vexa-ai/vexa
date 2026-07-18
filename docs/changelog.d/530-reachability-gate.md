- **Bots fail fast when the control plane is unreachable, instead of an opaque crashloop (#530).** On a
  freshly autoscaled k8s node whose network hasn't converged, a bot's first callback to meeting-api can
  be unreachable. The bot now makes its first `joining` lifecycle emit load-bearing: if the meeting-api
  callback is unreachable it probes redis, and only if BOTH control-plane channels are down does it
  refuse to join — terminating in under a few seconds with a dedicated exit code (3) and an attributed
  terminal (`failure_stage: requested`, `infra_fault: control_plane_unreachable`) rather than a generic
  `join_failure` or a stuck-`requested` meeting. A reachable control plane adds zero latency (the
  secondary channel is never probed). Operators can now tell "broken node" from "broken join" in one
  `kubectl describe`. See [Kubernetes deployment](/deployment-kubernetes) and [Troubleshooting](/troubleshooting).
