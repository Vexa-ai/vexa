- **Kubernetes: bots no longer tape meetings by default.** The Helm chart sets the new
  `diagnostics.captureSignal` value to `"false"`, so a bot spawned on Kubernetes stores no
  captured-signal tape (raw audio, captions, speaker events) unless an operator turns it on, fleet-wide
  or per account. Compose and Lite are unchanged. An unrecognized value stops the render, and
  admin-api and meeting-api refuse to boot on one. See
  [Captured-signal tapes](/deployment-kubernetes#captured-signal-tapes-off-by-default).
