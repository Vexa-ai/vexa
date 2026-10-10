- **Spawned bots and agent workers run with fewer privileges (#1784).** On Docker and Kubernetes every
  spawned container drops all Linux capabilities except the few an agent worker needs to give the
  model's tools a user of their own (a meeting bot keeps none), and can gain none (`no-new-privileges`;
  on Kubernetes `allowPrivilegeEscalation: false` and the `RuntimeDefault` seccomp profile). On
  OpenShift's restricted SCC set `runtime.workloadScheduling.agentWorker.capabilities: []`.
- **Kubernetes: broad tolerations need an explicit opt-in (#1784).** A spawned Pod's toleration with
  no key (it tolerates every taint) or for a control-plane or system taint is refused when the runtime
  boots, in `runtime.tolerations` / `global.tolerations` as in the per-class lists, unless
  `runtime.workloadScheduling.allowBroadTolerations: true`. **If your `global.tolerations` carry one,
  set that before upgrading**, or the runtime will not start.
- **Kubernetes: a chat's next turn starts again after the previous turn's Pod finished (#1784).** The
  runtime replaces its own finished Pod of the same name instead of failing the spawn, never touches a
  Pod it did not start, and removes a finished Pod once its exit is recorded. Spawned Pods now carry
  the release as `runtime.instance`, and the runtime re-adopts only its own release's Pods: Pods
  started by an older runtime are not re-adopted, so upgrade between meetings.
