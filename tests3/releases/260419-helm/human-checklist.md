# 260419-helm — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**helm**
- dashboard:   http://172.237.148.77:30001
- /meetings:   http://172.237.148.77:30001/meetings
- gateway:     http://172.237.148.77:30056
- kubectl:     `export KUBECONFIG=/home/dima/dev/vexa/tests3/.state-helm/lke_kubeconfig`

## Always

**Helm / LKE**
- [x] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [x] Open http://172.237.148.77:30056/ → gateway root JSON <!-- h:f0242b3d -->
- [x] Open http://172.237.148.77:30001/ → dashboard renders <!-- h:22e1c50d -->
- [x] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

**Release integrity**
- [x] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [x] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

**helm-chart-tuning** _(helm)_
- [x] [helm] kubectl get pods -l app.kubernetes.io/name=vexa -o json \ <!-- h:2090353a -->
  | jq '[.items[].spec.containers[] | {name, resources}]'
 → every container entry shows non-empty
resources.requests.{cpu,memory} AND resources.limits.{cpu,memory}.

- [x] [helm] kubectl get pdb -A → PodDisruptionBudget objects are available (may be 0 if disabled by values; template presence is the DoD). <!-- h:023e8188 -->

**helm-fresh-evidence** _(helm)_
- [x] [helm] kubectl get pods --all-namespaces → every vexa-owned pod is Running; 0 CrashLoopBackOff; 0 Error. <!-- h:d9a846f1 -->
- [x] [helm] kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail -20 → no new Warning events during install / first 5 min of steady state. <!-- h:a8be3c94 -->

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._