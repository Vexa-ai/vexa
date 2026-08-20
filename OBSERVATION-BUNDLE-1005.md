# Observation bundle — #1005 · runtime workloads carry CPU/memory requests and limits

**Issue:** [Vexa-ai/vexa#1005](https://github.com/Vexa-ai/vexa/issues/1005) — *Quota-controlled
clusters start every runtime workload — apply resources to bot and agent-worker Pods*
**Branch:** `1005-runtime-resources` · **base:** `616778fe394919f871d792fd6214ea23d0aeac6e` (origin/main)
**Implementation commit (the anchor for every row below):** `b3bed7b8` — *fix(runtime): spawned Pods
declare CPU/memory requests and limits (#1005)*. Not pushed; the founder opens the PR (the
contribution-rights declaration is a human decision, per `CONTRIBUTOR_RIGHTS.md`).
**Validation cluster:** k3d `k3d-vexa1005`, Kubernetes **v1.35.5+k3s1**, single server node, Docker 29.1.3
**Quota namespace:** `vexa-quota` with a `ResourceQuota` naming `requests.cpu`, `requests.memory`,
`limits.cpu`, `limits.memory` — the shape that forces every container to declare all four.

Facts first; my reading is labelled and lives at the end.

---

## The acceptance table

| # | Observation | Verdict | Evidence |
|---|---|---|---|
| **A1** | Meeting-bot Pod carries configured CPU/memory requests and limits and is admitted under quota | **GREEN** | [A1](#a1--meeting-bot-admitted-under-quota) |
| **A2** | Agent-worker Pod carries **independently** configured requests and limits and is admitted under the same quota | **GREEN** | [A2](#a2--agent-worker-admitted-independently-sized) |
| **A3** | Image, command, env, labels, scheduling and workspace mounts survive resource injection | **GREEN** (live + offline, with the negative control shown red) | [A3](#a3--every-generated-field-survives) |
| **A4** | Omitting one required request or limit makes admission red; restoring it makes the same spawn green | **GREEN** | [A4](#a4--red-green-on-one-missing-dimension) |
| **A5** | Both profiles reach truthful bounded terminal states on a real API server | **GREEN** | [A5](#a5--truthful-bounded-terminal-states) |
| **A-** | Runtime, Helm, docs-current, architecture and repository gates green at head | **GREEN** | [A-](#a---gates) |

**Coverage caveat carried through every live row:** the live legs ran with `busybox:1.36`
substituted for the meeting-bot and agent-worker images (`BROWSER_IMAGE` / `AGENT_WORKER_IMAGE`),
so what is proven is the **resource declaration and admission path through the real kernel, the real
profile registry and a real API server** — not that the 3.6 GB bot image boots inside those limits.
Sizing the real images for real meetings is the operator decision the chart now exposes; see
[Honest limits](#what-was-not-checked).

---

## A1 · Meeting-bot admitted under quota

**Expected:** with `RUNTIME_BOT_CPU=1` / `RUNTIME_BOT_MEMORY_MB=512`, a `meeting-bot` workload created
through the real `Runtime` + `K8sBackend` is admitted into `vexa-quota`, and the accepted Pod object
carries `requests` **and** `limits` for cpu and memory.

**Negative control (current head, pre-change shape):** unset all four sizing knobs — the same spawn is
rejected because it declares nothing. Run first:

```
=== MODE: red-unsized ===
--- profile=meeting-bot workloadId=mtg-red-unsized ---
SPAWN REJECTED: StartFailed: kubectl create -f - -n vexa-quota failed: Error from server (Forbidden):
error when creating "STDIN": pods "vexa-mtg-red-unsized" is forbidden: failed quota:
require-requests-and-limits: must specify limits.cpu for: vexa-mtg-red-unsized;
limits.memory for: vexa-mtg-red-unsized; requests.cpu for: vexa-mtg-red-unsized;
requests.memory for: vexa-mtg-red-unsized
```

**Actual (sized):**

```
=== MODE: green ===
--- profile=meeting-bot workloadId=mtg-green ---
kernel state: running
ADMITTED. container.resources = {"limits": {"cpu": "1", "memory": "512Mi"},
                                 "requests": {"cpu": "1", "memory": "512Mi"}}
image = busybox:1.36 | command = ['sleep', '120']
labels = {"runtime.managed": "true", "runtime.workload_id": "mtg-green"}
env names = ['VEXA_X']
phase = Running | qosClass = Guaranteed
terminal: destroyed
```

**Verdict: GREEN.** Red→green on the same spawn, same namespace, same quota. (`1000m` is normalized to
`1` by the API server; the submitted manifest carries `1000m`.)

---

## A2 · Agent-worker admitted, independently sized

**Expected:** in the SAME run and SAME namespace, `profile=agent` is admitted carrying a **different**
size, sourced from its own knobs (`RUNTIME_AGENT_WORKER_CPU=0.25` / `RUNTIME_AGENT_WORKER_MEMORY_MB=256`).

**Negative control:** same `red-unsized` run —

```
--- profile=agent workloadId=agent-red-unsized ---
SPAWN REJECTED: StartFailed: … pods "vexa-agent-red-unsized" is forbidden: failed quota:
require-requests-and-limits: must specify limits.cpu …; limits.memory …; requests.cpu …;
requests.memory for: vexa-agent-red-unsized
```

**Actual:**

```
--- profile=agent workloadId=agent-green ---
kernel state: running
ADMITTED. container.resources = {"limits": {"cpu": "250m", "memory": "256Mi"},
                                 "requests": {"cpu": "250m", "memory": "256Mi"}}
phase = Running | qosClass = Guaranteed
terminal: destroyed
```

**Verdict: GREEN.** `250m/256Mi` for the agent vs `1/512Mi` for the bot, in one run — the two classes
are genuinely sized apart, not off one shared knob. Offline counterparts:
`test_profile_default_applies_when_the_caller_omits_resources`,
`test_default_registry_sizes_the_two_profiles_independently`.

---

## A3 · Every generated field survives

**Expected:** a Pod carrying resources **and** workspace volumeMounts **and** the runtime's scheduling
constraints is accepted with its image, command, env, labels and restart policy intact.

**Negative control (the mechanism this change replaced), run live on the same cluster:**

```
$ kubectl run vexa-neg-control --image=busybox:1.36 --restart=Never -n vexa-quota \
    --overrides='{"spec":{"containers":[{"name":"vexa-neg-control","resources":{…}}]}}'
The Pod "vexa-neg-control" is invalid: spec.containers[0].image: Required value
```

A partial `containers` entry in `kubectl run --overrides` is a JSON **merge** patch: it replaces the
generated containers list wholesale and the image/env/command are gone. The issue predicted this; it
reproduces exactly.

**Actual (this change's mechanism — a complete Pod manifest submitted via `kubectl create -f -`),
read back from the API server after acceptance:**

```
ACCEPTED BY THE API SERVER — surviving fields:
  image        : busybox:1.36
  command      : ['sleep', '120']
  env          : [VEXA_BOT_CONFIG, VEXA_WORKSPACE_MOUNT_SOURCE, VEXA_WORKSPACE_MOUNT_TARGET, VEXA_MOUNTS]
  labels       : {"runtime.managed": "true", "runtime.workload_id": "a3-survive"}
  restartPolicy: Never
  tolerations  : [{"effect": "NoSchedule", "key": "vexa.ai/bots", "operator": "Exists"}]
  nodeSelector : {"kubernetes.io/os": "linux"}
  volumes      : [{"name": "workspace-store", "persistentVolumeClaim": {"claimName": "vexa-workspaces-a3"}}]
  volumeMounts : [{"mountPath": "/workspaces/u1", "name": "workspace-store", "subPath": "u1"},
                  {"mountPath": "/workspaces/deal-9", "name": "workspace-store", "readOnly": true,
                   "subPath": "deal-9"}]
  resources    : {"limits": {"cpu": "500m", "memory": "256Mi"},
                  "requests": {"cpu": "500m", "memory": "256Mi"}}
```

**Verdict: GREEN.** Resources and per-mount volumeMounts coexist on one container — the combination
the old override mechanism could not produce. Offline counterpart:
`test_pod_carries_resources_and_every_pre_existing_field`.

**Bonus finding, closed by the same change:** the pre-change code emitted a partial `containers`
override *whenever a workspace mount set was present* (`k8s_backend.py:102-103` at the base commit),
which is exactly the shape shown red above. Any k8s spawn carrying a workspace PVC was therefore
rejected at the API server before this change. Nothing in the issue named it; it fell out of building
the complete Pod.

---

## A4 · Red→green on one missing dimension

**Expected:** declare cpu but omit memory ⇒ admission red, naming only the memory dimension; restore
memory ⇒ the same spawn green.

**Actual (memory omitted, both profiles):**

```
=== MODE: red-no-memory ===
--- profile=meeting-bot workloadId=mtg-red-no-memory ---
SPAWN REJECTED: … is forbidden: failed quota: require-requests-and-limits:
must specify limits.memory for: vexa-mtg-red-no-memory; requests.memory for: vexa-mtg-red-no-memory

--- profile=agent workloadId=agent-red-no-memory ---
SPAWN REJECTED: … must specify limits.memory for: vexa-agent-red-no-memory;
requests.memory for: vexa-agent-red-no-memory
```

The cpu complaints are **absent** — the cpu declaration was accepted; only the omitted dimension reds.
Restoring it is the A1/A2 `green` run above (same script, same namespace, same quota).

**Verdict: GREEN.**

---

## A5 · Truthful bounded terminal states

**Expected:** a spawned workload reaches `Running` on a real API server, and `stop` → `destroy` drive
the kernel to `destroyed` with the Pod actually removed. A rejected spawn records an honest terminal
state rather than a false success.

**Actual:**

- both green profiles: `phase = Running`, `qosClass = Guaranteed`, then `terminal: destroyed`;
- every rejection above surfaced as `StartFailed` carrying the API server's own reason text — the
  kernel's existing contract (persist `stopped`/`start_failed`, emit the event, then raise) held
  through the new submit path;
- `tests/test_k8s_backend.py::test_k8s_backend_real_pod_lifecycle` — the repo's own cluster-dependent
  lifecycle test — **ran** against this cluster instead of skipping, and passed (create → Pod exists →
  `Running` → stop → destroy → Pod gone) through the rewritten `kubectl create -f -` path.

**Verdict: GREEN.**

---

## A- · Gates

```
$ COMPOSE_PROJECT=vexa-gate-1005 ONNXRUNTIME_NODE_INSTALL=skip node scripts/gates.mjs all
```

All 34 gates green, including:

| Gate | Result |
|---|---|
| `gate:python` | 12 packages · pytest green (runtime: **178 passed**, 0 skipped — up from 159 passed / 1 skipped at base) |
| `gate:config-contract` | 5 services · 207 declared keys · declarations ≡ deploy surfaces ≡ code reads (the 4 new `RUNTIME_*` keys declared and rendered) |
| `gate:compose` | REAL compose stack proven bot-ready (health · auth · transcript · recording · control-plane) — the Compose regression leg for the Backend port change |
| `gate:graph-py` / `gate:isolation-py` | the new `backend → models`, `profiles → models` edges are allowed |
| `gate:schema` / `gate:contract-version` | 25 sealed contracts unchanged — runtime.v1 was **not** touched |
| `gate:docs-version` | docs reflect v0.12.18 ≡ Chart.yaml appVersion |

`deploy/helm/tests/test_template.sh` → `gate:helm PASS`.

**Two environmental reds, both resolved, neither caused by this diff.** The first `gates all` run failed
`gate:compose` with `Error response from daemon: No such container: dcfe28d7c034…`. That container is
a **2-week-old exited container from another checkout** (label
`com.docker.compose.project=vexa-compose-gate`) holding the `vexa-compose-gate_redis-data` volume; the
daemon holds a dangling reference the volume cannot be freed from. The conftest ships
`COMPOSE_PROJECT` explicitly "override on a shared host" — running under `vexa-gate-1005` is green.
This diff touches no compose file.

**The re-run at the committed head could not complete — the host, not the code.** After the green run
the only edits were docstring/comment corrections in `k8s_backend.py`, `mounts.py` and
`test_mounts.py` (folded into the same commit; no behaviour change). Re-verifying them ran into a
degraded host, in three stages, each recorded rather than retried away:

1. `gates all` died with `zsh: no space left on device` during `pytest core/agent` — the volume was
   down to **1.6 GiB free** after the k3d cluster and the compose image builds. I tore down the k3d
   cluster and the `vexa-gate-1005` compose project to return the space (2.8 GiB free after).
2. `gate:python` then blocked indefinitely at `core/identity/services/admin-api` — a **concurrent
   session** in a separate local checkout was running that same package's
   testcontainer suite at the same time. Both processes sat at 0% CPU. `admin-api` is untouched by
   this diff.
3. The runtime suite then hung too. Bisected to exactly one file: **`tests/test_docker_backend.py`**,
   which drives the real Docker socket. By then `docker ps` itself did not return — the Docker
   Desktop daemon was wedged under the disk pressure.

What DID run at the committed head, in ~2.5 s total:

| Selection | Result |
|---|---|
| whole runtime suite minus `test_docker_backend.py`, `test_worker_image.py`, `test_readopt.py`, `test_start_conflict.py` | **149 passed, 1 skipped** |
| `test_readopt.py` + `test_start_conflict.py` | **20 passed** |
| `test_worker_image.py` | **7 passed** |
| the four files touched by the comment-only amend | **58 passed** |

**176 passed / 1 skipped** across those selections (the skip is the k8s cluster test, once the k3d
cluster was torn down). Only `test_docker_backend.py` is unaccounted for at the exact head SHA — and
it passed in the full green run above, on the same code.

**So: re-run `gates all` on a quiet host with disk headroom before the PR opens.** Nothing is
suspected; the claim simply deserves to sit on the exact head SHA rather than on its
comment-identical parent.

---

## Helm render evidence (issue's required Helm leg)

Rendered from `deploy/helm/charts/vexa` with `-f values-test.yaml`:

| Case | `RUNTIME_BOT_CPU` / `_MEMORY_MB` | `RUNTIME_AGENT_WORKER_CPU` / `_MEMORY_MB` |
|---|---|---|
| shipped defaults | `"1"` / `"2048"` | `"0.5"` / `"1024"` |
| distinct overrides (`--set …meetingBot.cpu=2,…memoryMb=4096,…agentWorker.cpu=0.25,…memoryMb=512`) | `"2"` / `"4096"` | `"0.25"` / `"512"` |
| all four set to `""` (the unset case) | `""` / `""` | `""` / `""` |
| `--set runtime.backend=docker` | not rendered (0 occurrences) | not rendered |

The unset case is the optional-contract preservation C3 asks for: empty ⇒ the profile builds no
`Resources` ⇒ the Pod declares nothing ⇒ exactly the pre-change spawn. Offline counterpart:
`test_default_registry_emits_no_resources_when_unset`.

---

## What changed (the seams)

| Seam | File | Change |
|---|---|---|
| C1 · resource intent crosses the port | `core/runtime/src/runtime_kernel/backend.py` | `Backend.start(..., resources: Optional[Resources] = None)` |
| C1 | `core/runtime/src/runtime_kernel/kernel.py` | `effective_resources = spec.resources or profile.resources`, passed to `backend.start`; `_coerce_registry` now accepts a full `Profile` |
| C1 | `core/runtime/src/runtime_kernel/models.py` | `Resources` fields floored at `ge=0`, mirroring the sealed schema's `minimum: 0` — a negative fails at parse, before any spawn |
| C2 | `core/runtime/src/runtime_kernel/k8s_backend.py` | new `resource_requirements()` + `build_pod()`; `start()` submits a complete manifest via `kubectl create -f -` instead of `kubectl run --overrides` |
| C3 | `core/runtime/src/runtime_kernel/profiles.py` | `Profile.resources`; per-class env knobs parsed in `default_registry()`, non-numeric fatal at boot |
| C3 | `deploy/helm/charts/vexa/values.yaml`, `templates/deployment-runtime.yaml` | `runtime.workloadResources.{meetingBot,agentWorker}.{cpu,memoryMb}` → 4 env keys (k8s backend only) |
| — | `core/runtime/src/runtime_kernel/{docker,process}_backend.py` | accept `resources`, do not enforce; the k8s-only boundary is stated in each docstring |
| docs | `docs/changelog.d/1005-runtime-resources.md`, `deploy/helm/charts/vexa/README.md` | changelog fragment (the `docs-current` touch) + a values/behaviour section |
| tests | `core/runtime/tests/test_workload_resources.py` (new, 16 tests) + 4 existing files updated | see below |

**Design decisions worth reviewing:**

1. **One value → both request and limit.** runtime.v1 carries one `cpu` and one `memoryMb`. Both map
   to request *and* limit, per the issue's "do not invent separate request/limit semantics inside
   sealed v1". Consequence: Guaranteed QoS, and `memoryMb` is a hard OOM ceiling.
2. **GPU maps to `limits["nvidia.com/gpu"]` only** — extended resources are limits-side in Kubernetes
   (the request is derived and a differing request is rejected). This is emission only; no device
   plugin, node pool or live GPU spawn was exercised.
3. **`0` is treated as unset.** Schema-legal, but a quota reads `cpu: 0` as a zero request, not as
   "unspecified" — emitting it would be worse than omitting it.
4. **The producers were not changed.** See the deviation note below.

---

## Deviation from the issue's trace

**The issue's trace is accurate at the base commit** — every `file:line` claim spot-checked and held
(`models.py:35-52` optional `Resources`; `kernel.py` calling `backend.start(workloadId, runnable,
effective_env)` and dropping `spec.resources`; `k8s_backend.py` generating `kubectl run` with no
container resources; `bot_spawn/invocation.py::build_workload_spec` and
`core/agent/shared/adapters.py::RuntimeHttpClient.spawn` emitting no resources).

Three departures, each with its reason:

1. **`kubectl run --dry-run=client -o json` — the issue's suggested generator — is not viable.**
   The issue offers it as "a viable fork". On kubectl **v1.34.1** it performs API discovery *before*
   generating and exits 1 with **zero stdout** when no server is reachable:

   ```
   $ kubectl run test1 --image=alpine --restart=Never --dry-run=client -o json
   EXIT=1 · stdout bytes: 0
   The connection to the server localhost:8080 was refused - did you specify the right host or port?
   ```

   Adopting it would also add an API round trip to every spawn and make the offline unit tests
   depend on a live cluster. I build the Pod object in Python instead (`build_pod`) and submit it
   with `kubectl create -f -` — the same "complete Pod object, merge resources by container name,
   submit it" the issue's C2 asks for, minus the generator.

2. **Producers were not given resource configuration; the per-profile default in the runtime is the
   configuration seam.** My task brief asked for producer-side emission; the issue's C3 asks for
   "explicit per-profile spawned-workload resource values … Profile defaults apply when callers omit
   resources; explicit workload resources override them." I followed the issue. Consequences:
   `bot_spawn/invocation.py` and `core/agent/shared/adapters.py` are untouched; sizing is a
   deployment concern in the chart, not per-request control-plane state; and the override path is
   live and tested (`test_explicit_spec_resources_override_the_profile_default`) for any producer
   that later wants per-workload sizing. Independent configurability — the actual requirement — is
   satisfied per class.

3. **Docker enforcement was not added.** The issue permits either ("if Docker enforcement is added,
   use native host config and test it. Otherwise document the k8s-only enforcement boundary without
   implying parity"). Compose has no admission controller to satisfy, and a `HostConfig.Memory`
   ceiling would newly OOM-kill live meeting bots that run unbounded today. Boundary documented in
   `backend.py`, both backend docstrings, and the chart README.

---

## Tests

New: `core/runtime/tests/test_workload_resources.py` — 16 tests, all offline.

| Concern | Test |
|---|---|
| spec resources reach the backend | `test_spec_resources_reach_the_backend` |
| profile default applies when omitted; two classes sized apart | `test_profile_default_applies_when_the_caller_omits_resources` |
| explicit spec wins over the profile default | `test_explicit_spec_resources_override_the_profile_default` |
| unconfigured ⇒ `None` (optional contract) | `test_unconfigured_profile_preserves_the_optional_contract` |
| negative values rejected before spawn | `test_negative_resources_are_rejected_before_spawn` (cpu/memory/gpu) |
| `0` treated as unset | `test_zero_resources_are_not_emitted` |
| v1 → requests+limits mapping | `test_resource_requirements_maps_v1_to_requests_and_limits` |
| GPU → limits only | `test_gpu_maps_to_the_limits_side_only` |
| A3 offline: every field survives | `test_pod_carries_resources_and_every_pre_existing_field` |
| no resources ⇒ no `resources` key | `test_pod_without_resources_omits_the_field_entirely` |
| scheduling shapes the Pod without leaking into container env | `test_runtime_scheduling_env_shapes_the_pod_without_becoming_container_config` |
| the submit is `create -f -` with the manifest on stdin | `test_k8s_start_submits_the_pod_via_create_stdin` |
| per-class env sizing; unset; partial (memory only) | `test_default_registry_sizes_the_two_profiles_independently`, `…_emits_no_resources_when_unset`, `test_partial_profile_sizing_is_honoured` |
| malformed sizing fatal at boot | `test_malformed_profile_sizing_is_fatal_at_boot` |

Updated (mechanism changed, semantics preserved): `test_k8s_command_wiring.py` (asserts the submitted
manifest instead of the `kubectl run` argv — the #675 guarantee that meeting-bot carries **no**
`command` is still asserted), `test_readopt.py` (adoption labels now read from the manifest),
`test_lifecycle.py` + `test_start_failed.py` (fake backends take the 4-arg port).

Runtime suite: **159 passed / 1 skipped at base → 178 passed / 0 skipped at head** (the skip
disappeared because a cluster was reachable, so the real-Pod lifecycle test actually ran).

---

## What was NOT checked

- **The real bot and agent-worker images under these limits.** Every live leg substituted
  `busybox:1.36`. Whether `vexaai/vexa-bot` runs inside `2048Mi` is untested; the shipped default
  is a starting point, not a measurement. **Nobody should treat the default as a validated bot
  memory budget.**
- **OpenShift restricted SCC on the SPAWNED Pods.** Untested here (k3d has no SCC). The reporting operator proved
  control-plane SCC; the spawned Pod is a *different* object and a restricted SCC may still require
  `runAsNonRoot` / dropped capabilities / a `seccompProfile` that this change does not add. Called
  out in the test kit as the top thing to watch.
- **GPU emission end-to-end.** `nvidia.com/gpu` mapping is unit-tested; no GPU node, device plugin or
  live GPU spawn was exercised.
- **A LimitRange-defaulted namespace.** The issue's closing rule ("LimitRange-defaulted fields do not
  prove Vexa emitted resources") is honoured by construction — `vexa-quota` has no LimitRange, so the
  values observed on the Pods came from Vexa. Behaviour when a LimitRange *also* exists (and whose
  values win) was not exercised.
- **Multi-node scheduling pressure.** Single-node k3d; Guaranteed QoS changes scheduling density on
  a real cluster and that was not measured.
- **`docs/docs/deployment-kubernetes.mdx`** was deliberately not edited — it is a known hot,
  non-union-mergeable file and the changelog fragment carries `docs-current`. The page should gain a
  paragraph on spawned-Pod resources in a sequenced follow-up (the issue's docs surface names it).
- **Lite/process substrate** beyond the signature change (it accepts and ignores; nothing to enforce).

---

## Exact commands run

```bash
# worktree, based on origin/main
git fetch origin
git worktree add -b 1005-runtime-resources .claude/worktrees/1005-runtime-resources origin/main

# offline suite (base, then head)
cd core/runtime && uv sync --group dev && uv run pytest -q

# helm
cd deploy/helm/charts/vexa
helm template vexa . -n vexa -f values-test.yaml
helm template vexa . -n vexa -f values-test.yaml \
  --set runtime.workloadResources.meetingBot.cpu=2 \
  --set runtime.workloadResources.meetingBot.memoryMb=4096 \
  --set runtime.workloadResources.agentWorker.cpu=0.25 \
  --set runtime.workloadResources.agentWorker.memoryMb=512
helm template vexa . -n vexa -f values-test.yaml \
  --set runtime.workloadResources.meetingBot.cpu="" --set runtime.workloadResources.meetingBot.memoryMb="" \
  --set runtime.workloadResources.agentWorker.cpu="" --set runtime.workloadResources.agentWorker.memoryMb=""
helm template vexa . -n vexa -f values-test.yaml --set runtime.backend=docker
sh deploy/helm/tests/test_template.sh

# live cluster
brew install k3d && k3d cluster create vexa1005 --agents 0 --wait
kubectl apply -f <quota-ns.yaml>      # namespace vexa-quota + ResourceQuota (yaml inlined in the kit)
cd core/runtime
PYTHONPATH=src uv run python <quota-validate.py> red-unsized     # A1/A2 negative control
PYTHONPATH=src uv run python <quota-validate.py> green           # A1/A2/A5
PYTHONPATH=src uv run python <quota-validate.py> red-no-memory   # A4
PYTHONPATH=src uv run python <a3-live.py>                        # A3
kubectl run vexa-neg-control --image=busybox:1.36 --restart=Never -n vexa-quota \
  --overrides='{"spec":{"containers":[{"name":"vexa-neg-control","resources":{…}}]}}'   # A3 control

# gates
COMPOSE_PROJECT=vexa-gate-1005 ONNXRUNTIME_NODE_INSTALL=skip node scripts/gates.mjs all
```

The three validation scripts (`quota-ns.yaml`, `quota-validate.py`, `a3-live.py`) are reproduced in
[`MARVIN-TEST-KIT-1005.md`](MARVIN-TEST-KIT-1005.md) so a non-author can re-run every live row.

---

## My reading (labelled as mine, downstream of the data)

The value the issue asks for is real and observed: both shipped workload classes are admitted into a
namespace that rejects undeclared containers, sized apart, with every pre-existing Pod field intact,
and the red control reproduces on demand. The two things I would not let ride into an external validation run
unstated are (a) the shipped `meetingBot.memoryMb: 2048` default is a *guess*, not a measurement, and
a bad guess becomes a mid-meeting OOM kill rather than a visible failure; and (b) the spawned-Pod SCC
question on OpenShift is genuinely open — that operator's earlier control-plane SCC proof does not carry
to a `kubectl create`d bare Pod. Both are in the test kit as the first things to look at.
