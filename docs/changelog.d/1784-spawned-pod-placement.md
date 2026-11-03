- **Bots and agent workers can be placed on their own pool, per class (#1784).** On Kubernetes,
  `runtime.workloadScheduling.meetingBot` and `.agentWorker` set the node selector, tolerations,
  priority class and image pull secrets of the Pods the runtime spawns. All are empty by default, so
  nothing changes until you set them. The runtime refuses to boot on a malformed value, and a caller
  cannot choose them. See [Kubernetes](/deployment-kubernetes).
