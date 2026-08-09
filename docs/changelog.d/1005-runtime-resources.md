- **Spawned bot and agent-worker Pods declare CPU/memory requests and limits, so quota-controlled
  namespaces admit them (#1005).** The runtime accepted `resources` on a `runtime.v1` WorkloadSpec
  but dropped it before the backend, so every dynamically created Pod was unsized — and a namespace
  whose policy requires each container to declare requests *and* limits (a `ResourceQuota`, a
  restricted OpenShift project) rejected both shipped workload classes at admission. Resource intent
  now crosses the backend port, and the Kubernetes backend submits a complete Pod manifest carrying
  the container's requests and limits alongside its image, command, env, labels, workspace mounts
  and scheduling. Size the two classes independently with the chart's `runtime.workloadResources`
  (`meetingBot` / `agentWorker`); each value sets both the request and the limit, and leaving a
  class empty keeps the previous unsized behaviour. Enforcement is Kubernetes-only — the docker and
  process backends accept the same intent without acting on it. See
  [Deployment](/deployment-kubernetes).
