# Test kit — #1005 on a quota-controlled cluster

For the validator running this on a real quota-enforcing namespace (OpenShift dev cluster included).
Branch `1005-runtime-resources`, based on `616778fe`.

**What you are proving:** the two Pods the runtime creates *dynamically* — the meeting bot and the
agent worker — now declare CPU and memory requests **and** limits, so your namespace admits them
instead of rejecting them. Nothing else about the release changes.

**What has already been proven, and where** — so you can skip re-doing it:

| Already green | Where |
|---|---|
| Both profiles admitted under a `ResourceQuota` requiring all four values, sized independently | k3d v1.35.5, namespace `vexa-quota` — see `OBSERVATION-BUNDLE-1005.md` A1/A2 |
| Image, command, env, labels, tolerations, nodeSelector, workspace mounts all survive | same, A3 |
| Removing one dimension reds admission; restoring it greens the same spawn | same, A4 |
| Offline runtime suite, Helm render, Compose stack, all 34 repo gates | same, A- |

**What only your cluster can prove:** OpenShift SCC behaviour on the spawned Pods, your real quota
headroom, and whether the real bot image lives inside the memory ceiling you set. Read
[the honest caveats](#honest-caveats) before you start — one of them is likely to bite.

---

## 1 · Install

Two values blocks matter. The first is the new one.

```yaml
# values-quota.yaml
runtime:
  backend: k8s
  # Sizing for the Pods the runtime SPAWNS. NOT runtime.resources (which sizes the runtime
  # Deployment itself). One value per dimension sets BOTH the request and the limit.
  workloadResources:
    meetingBot:
      cpu: 2                 # → requests/limits cpu: 2000m
      memoryMb: 4096         # → requests/limits memory: 4096Mi
    agentWorker:
      cpu: 0.5               # → requests/limits cpu: 500m
      memoryMb: 1024         # → requests/limits memory: 1024Mi
```

```bash
helm upgrade --install vexa deploy/helm/charts/vexa \
  -n <your-namespace> --create-namespace \
  -f values-quota.yaml \
  --set global.imageTag=<TAG> \
  --set secrets.adminApiToken=$ADMIN_TOKEN \
  --set secrets.internalApiSecret=$INTERNAL_API_SECRET
```

Confirm the sizing reached the runtime before you spawn anything:

```bash
kubectl -n <your-namespace> get deploy vexa-runtime -o json \
  | jq '.spec.template.spec.containers[0].env[]
        | select(.name|test("^RUNTIME_(BOT|AGENT_WORKER)_"))'
```

Expected — four entries, non-empty:

```json
{"name":"RUNTIME_BOT_CPU","value":"2"}
{"name":"RUNTIME_BOT_MEMORY_MB","value":"4096"}
{"name":"RUNTIME_AGENT_WORKER_CPU","value":"0.5"}
{"name":"RUNTIME_AGENT_WORKER_MEMORY_MB","value":"1024"}
```

If they are empty strings, the chart values did not land — the spawned Pods will declare nothing and
your namespace will reject them exactly as before. Fix this before going further.

> **Sizing note, not a formality.** `memoryMb` is a **hard ceiling**: a workload exceeding it is
> OOM-killed. Before this change nothing was enforced, so bots ran unbounded and we have no measured
> figure to hand you. `4096` above is deliberately more generous than the chart default (`2048`).
> Start high, watch actual usage, then tighten — do not start at the default and discover the ceiling
> as a dropped meeting.

---

## 2 · Spawn both profiles and watch

Trigger one meeting bot (your normal `POST /bots` path) and one agent dispatch. Then, for each:

```bash
kubectl -n <your-namespace> get pods -l runtime.managed=true
kubectl -n <your-namespace> get pod <pod> -o json | jq '{
  qos:        .status.qosClass,
  phase:      .status.phase,
  resources:  .spec.containers[0].resources,
  image:      .spec.containers[0].image,
  command:    .spec.containers[0].command,
  envCount:   (.spec.containers[0].env|length),
  labels:     .metadata.labels
}'
```

### Expected observations

| Pod | Name shape | Expect |
|---|---|---|
| meeting bot | `vexa-<meeting-workload-id>` | `resources.requests` **and** `.limits` = `{cpu: 2, memory: 4096Mi}`; `qosClass: Guaranteed`; `phase: Running`; **no** `command` (the bot image's own ENTRYPOINT boots it); `labels` carry `runtime.managed=true` + `runtime.workload_id` |
| agent worker | `vexa-agent-…` | `resources` = `{cpu: 500m, memory: 1Gi}` — **different from the bot**; `qosClass: Guaranteed`; `command: ["python","-m","worker"]` |

`2000m`/`500m` display as `2`/`500m` — the API server normalizes; either spelling is the same value.

Quota consumption should move:

```bash
kubectl -n <your-namespace> describe resourcequota
```

Both classes should appear in `requests.cpu` / `limits.memory` usage while running, and drop back when
the workloads finish.

### The one-line red control (optional, 30 seconds)

Proves the quota is really enforcing and that the values came from Vexa, not from a LimitRange:

```bash
kubectl -n <your-namespace> run quota-probe --image=busybox:1.36 --restart=Never -- sleep 30
```

Expected: **rejected** — `must specify limits.cpu … requests.memory …`. If this *succeeds*, a
LimitRange is defaulting your namespace, and per the issue's closing rule a green run there does not
prove Vexa emitted the resources. Re-check with the `jq` above: the values must equal what you set in
`values-quota.yaml`, not the LimitRange defaults.

---

## 3 · Capture this on failure

Please attach all of it — partial reports cost a round trip.

```bash
NS=<your-namespace>; POD=<the failing pod>

# 1. Did admission reject the spawn? The runtime logs the API server's own reason verbatim.
kubectl -n $NS logs deploy/vexa-runtime --tail=200 | grep -iE "kubectl create|forbidden|StartFailed"

# 2. Namespace events — quota denials, SCC denials, scheduling failures all land here
kubectl -n $NS get events --sort-by=.lastTimestamp | tail -40

# 3. The Pod as the API server accepted it (or the whole object if it never got created)
kubectl -n $NS get pod $POD -o yaml

# 4. Why it is not running
kubectl -n $NS describe pod $POD

# 5. What the workload itself said
kubectl -n $NS logs $POD --previous --tail=200 || kubectl -n $NS logs $POD --tail=200

# 6. Quota state at the time
kubectl -n $NS describe resourcequota
kubectl -n $NS get limitrange -o yaml     # if any — see the LimitRange note above
```

Reading the three failure shapes:

| Symptom | Almost certainly |
|---|---|
| No Pod at all; runtime log says `forbidden: failed quota: … must specify …` | the sizing env did not reach the runtime, or one dimension is empty — recheck §1 |
| No Pod; message mentions `unable to validate against any security context constraint` | **SCC**, not quota — see caveats below |
| Pod exists, `Pending`, events say `Insufficient cpu/memory` | your quota or node headroom is smaller than the size you set — lower `cpu`/`memoryMb` or raise the quota |
| Pod runs then dies with `OOMKilled` (exit 137) | `memoryMb` is too low for the real workload — raise it |

---

## Honest caveats

**1 · Spawned-Pod SCC on OpenShift is untested — this is the most likely surprise.**
You proved the *control plane* satisfies your SCC. The bot and agent Pods are **different objects**:
bare Pods created by the runtime at spawn time, not children of any Deployment, so they inherit
nothing from the control-plane pod templates. A `restricted-v2` SCC may additionally require
`runAsNonRoot`, a dropped capability set, or a `seccompProfile` on those Pods — **this change adds
none of those**, and no OpenShift cluster was available to test it. If §2 fails with an SCC message
rather than a quota message, that is a *separate, known-open* gap: capture the event text and the SCC
name (`kubectl -n $NS get pod $POD -o jsonpath='{.metadata.annotations.openshift\.io/scc}'`) and send
it — it is a follow-up issue, not a defect in this one.

**2 · The memory ceiling is a guess until you measure it.** No live meeting has run under an enforced
limit. Set generously, observe (`kubectl top pod`), then tighten.

**3 · Enforcement is Kubernetes-only.** The docker and process backends accept the same resource
intent and do not act on it. Compose and Lite deployments are unchanged; no parity is claimed.

**4 · Guaranteed QoS changes scheduling density.** Request equals limit by design (runtime.v1 carries
one value per dimension). Reserved capacity is now real — a node that used to fit N unbounded bots
fits `floor(allocatable / size)` of them.

**5 · What the k3d validation substituted.** The live legs in the bundle ran `busybox:1.36` in place
of the bot and worker images, so the *declaration and admission* path is proven against a real API
server but the real images were never run inside the limits. Your run is the first that does that —
which is exactly why it is the one that closes the issue.

---

## Appendix · the exact scripts used for the recorded evidence

Re-runnable against any cluster; drop them in a scratch dir.

<details><summary><code>quota-ns.yaml</code></summary>

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: vexa-quota
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: require-requests-and-limits
  namespace: vexa-quota
spec:
  hard:
    requests.cpu: "4"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 4Gi
    pods: "10"
```
</details>

<details><summary><code>quota-validate.py</code> — spawns both profiles through the real kernel</summary>

Run from `core/runtime` as
`PYTHONPATH=src uv run python quota-validate.py {green|red-unsized|red-no-memory}`.

```python
import json, os, subprocess, sys, time
NS = "vexa-quota"

def kubectl(*args, stdin=None, check=True):
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True, input=stdin)
    if check and r.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args)} FAILED:\n{r.stderr}")
    return r

def pod_json(name):
    return json.loads(kubectl("get", "pod", name, "-n", NS, "-o", "json").stdout)

def spawn(profile_name, workload_id):
    from runtime_kernel import Runtime
    from runtime_kernel.k8s_backend import K8sBackend
    from runtime_kernel.models import WorkloadSpec
    from runtime_kernel.profiles import apply_command_overrides, default_registry
    rt = Runtime(backend=K8sBackend(namespace=NS),
                 profiles=apply_command_overrides(default_registry()), grace_sec=5.0)
    return rt, rt.create(WorkloadSpec(workloadId=workload_id, profile=profile_name,
                                      env={"VEXA_X": "y"}))

def main():
    os.environ["BROWSER_IMAGE"] = os.environ["AGENT_WORKER_IMAGE"] = "busybox:1.36"
    os.environ["AGENT_IMAGE"] = "busybox:1.36"
    os.environ["AGENT_WORKER_COMMAND"] = os.environ["BOT_COMMAND"] = "sleep 120"
    mode = sys.argv[1] if len(sys.argv) > 1 else "green"
    if mode == "green":
        os.environ["RUNTIME_BOT_CPU"] = "1";            os.environ["RUNTIME_BOT_MEMORY_MB"] = "512"
        os.environ["RUNTIME_AGENT_WORKER_CPU"] = "0.25"; os.environ["RUNTIME_AGENT_WORKER_MEMORY_MB"] = "256"
    elif mode == "red-no-memory":
        os.environ["RUNTIME_BOT_CPU"] = "1";            os.environ.pop("RUNTIME_BOT_MEMORY_MB", None)
        os.environ["RUNTIME_AGENT_WORKER_CPU"] = "0.25"; os.environ.pop("RUNTIME_AGENT_WORKER_MEMORY_MB", None)
    elif mode == "red-unsized":
        for k in ("RUNTIME_BOT_CPU", "RUNTIME_BOT_MEMORY_MB",
                  "RUNTIME_AGENT_WORKER_CPU", "RUNTIME_AGENT_WORKER_MEMORY_MB"):
            os.environ.pop(k, None)

    print(f"=== MODE: {mode} ===")
    for profile, wid in (("meeting-bot", f"mtg-{mode}"), ("agent", f"agent-{mode}")):
        kubectl("delete", "pod", f"vexa-{wid}", "-n", NS, "--ignore-not-found",
                "--grace-period=0", "--force", check=False)
        print(f"\n--- profile={profile} workloadId={wid} ---")
        try:
            rt, status = spawn(profile, wid)
        except Exception as exc:
            print(f"SPAWN REJECTED: {type(exc).__name__}: {str(exc)[:600]}")
            continue
        print(f"kernel state: {status.state.value}")
        c = pod_json(f"vexa-{wid}")["spec"]["containers"][0]
        print("ADMITTED. container.resources =", json.dumps(c.get("resources", {})))
        print("image =", c["image"], "| command =", c.get("command"))
        deadline, phase = time.time() + 60, ""
        while time.time() < deadline:
            phase = pod_json(f"vexa-{wid}")["status"]["phase"]
            if phase == "Running":
                break
            time.sleep(2)
        print("phase =", phase, "| qosClass =", pod_json(f"vexa-{wid}")["status"].get("qosClass"))
        rt.stop(wid); rt.destroy(wid)
        print("terminal:", rt.get(wid).state.value)

if __name__ == "__main__":
    main()
```
</details>

<details><summary><code>a3-live.py</code> — proves no generated field is erased</summary>

Creates a small PVC, spawns one Pod carrying resources + tolerations + nodeSelector + a two-mount
workspace set, and prints what the API server accepted. Full source in the session scratch; the
essential call is:

```python
K8sBackend(namespace=NS).start(
    wid, Runnable(image="busybox:1.36", command=["sleep", "120"]), env,
    Resources(cpu=0.5, memoryMb=256),
)
```

with `RUNTIME_K8S_TOLERATIONS` / `RUNTIME_K8S_NODE_SELECTOR` set in the process env and
`VEXA_WORKSPACE_MOUNT_SOURCE` / `_TARGET` / `VEXA_MOUNTS` in the workload env. Then read the Pod back
and assert `image`, `command`, `env`, `labels`, `restartPolicy`, `tolerations`, `nodeSelector`,
`volumes`, `volumeMounts` and `resources` are all present on the accepted object.
</details>
