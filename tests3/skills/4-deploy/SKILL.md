---
name: 4-deploy
description: "Invoke when the user wants to push the current dev code to the provisioned Vexa deployments — build a fresh :dev image tag, publish to DockerHub, pull on every provisioned VM/cluster, restart services. Use when the user says 'deploy to the VMs', 'push the fix', 'redeploy', 'push dev to the cluster', 'rebuild :dev', or any time after stage 3 (local commits on dev) and before stage 5 (iterate)."
---

## Stage 4 of 9 — deploy

Builds a fresh `:dev` timestamp tag, publishes to DockerHub, and redeploys every mode listed in the scope. Keeps volumes (DB + Redis + MinIO state preserved across iterations). Fresh-reset happens at stage 6, not here.

## Command

```bash
make release-deploy SCOPE=$SCOPE
```

## What it does

1. `make release-build` — builds every image with a fresh timestamp tag (e.g. `0.10.0-260417-1830`), publishes to DockerHub, writes the tag to `deploy/compose/.last-tag` and into `tests3/.state-{lite,compose,helm}/image_tag`.
2. For each mode in `scope.deployments.modes`:
   - `lite` → SSH: `git fetch origin dev && git reset --hard origin/dev && docker pull vexaai/vexa-lite:dev && docker rm -f vexa-lite && make lite`.
   - `compose` → SSH: `git fetch origin dev && git reset --hard origin/dev && docker compose pull && docker compose up -d --force-recreate`.
   - `helm` → `helm upgrade` via `lke-setup-helm.sh` (keeps PVCs).

All three redeploys run in parallel. Volumes are preserved — this is a fast iteration redeploy, not a clean reset.

## Prerequisites

- Stage 1 + 2 done: `SCOPE` set + every `scope.deployments.modes` mode provisioned.
- Stage 3 committed + pushed to `origin/dev` (the VMs pull from there).
- DockerHub auth configured locally (`docker login vexaai` or credential helper).

## Verify

```bash
TAG=$(cat deploy/compose/.last-tag)
echo "built tag: $TAG"
for mode in $(python3 -c "import yaml; print(' '.join(yaml.safe_load(open('$SCOPE'))['deployments']['modes']))"); do
    echo "$mode running tag: $(cat tests3/.state-$mode/image_tag 2>/dev/null)"
done
```

## Next

→ stage 5: `make release-iterate SCOPE=$SCOPE` — run scope-filtered tests to see if the fix landed.
