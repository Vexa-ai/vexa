- **Helm: agent-api no longer deadlocks on upgrade (#774).** agent-api mounts the single
  `agent-workspaces` PVC, whose default access mode is `ReadWriteOnce`; the shared zero-downtime
  RollingUpdate (`maxSurge:1`) made any pod-spec-changing `helm upgrade` stall on a Multi-Attach
  error. agent-api now renders `strategy: Recreate` under an RWO workspace (the same opt-out redis
  already carries), and keeps RollingUpdate when the workspace is `ReadWriteMany`.
