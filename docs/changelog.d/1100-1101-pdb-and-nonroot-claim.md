- **Single-replica components no longer deadlock node drains ([#1100](https://github.com/Vexa-ai/vexa/issues/1100)).**
  The chart's default PodDisruptionBudgets were `minAvailable: 1`, which allows **zero** disruptions
  when a component runs one replica — `kubectl drain` hangs forever and a cluster operator cannot
  cordon the node, while protecting nothing (a single pod has no peer to fail over to). The shipped
  budgets are now `maxUnavailable: 1`, and `templates/pdb.yaml` enforces it independently: below two
  replicas it emits `maxUnavailable: 1` whatever the values say, so an operator carrying an older
  `minAvailable` override forward cannot deadlock their own drains either. At two replicas the two
  expressions are equivalent; at three or more `maxUnavailable: 1` is stricter (one eviction at a
  time), which is the intended trade. Reported by an operator running the chart on a
  quota-controlled OpenShift cluster, whose only remedy was to disable the PDB by hand.
- **The chart no longer claims the images run non-root ([#1101](https://github.com/Vexa-ai/vexa/issues/1101)).**
  `values.yaml` asserted that images "run non-root via their Dockerfile USER". No Dockerfile in this
  repository contains a `USER` directive — 0 of 15 — so every image starts as its base image's
  default UID, which is root. The comment now states that, gives the command that verifies it, and
  explains why `runAsNonRoot`/`runAsUser` are deliberately absent rather than set to an unverified
  hardened-looking value. No behavioural change.
- **Spawned bot and agent-worker Pods can carry a security context ([#1102](https://github.com/Vexa-ai/vexa/issues/1102)).**
  New `runtime.workloadSecurityContext.{pod,container}`. A spawned Pod is a bare `kubectl run` Pod
  that inherits nothing from the runtime Deployment, so until now it carried no security context at
  all — the likeliest rejection on a namespace enforcing OpenShift `restricted-v2` or the upstream
  Pod Security *restricted* profile, and untested, since the only cluster validation this path has
  had is k3d, which has no SCC admission. Both halves **default to empty and empty omits the field
  entirely**, so an install that leaves them alone renders byte-identically. The default is absent
  rather than hardened on purpose: whether these images tolerate an arbitrary non-root UID is an
  open question tracked at #1102, and a `runAsNonRoot: true` default would break every existing
  operator's bots to satisfy one cluster's policy.
