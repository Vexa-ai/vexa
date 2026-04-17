---
name: 2-provision
description: "Invoke this when the user wants to provision fresh deployments for a Vexa release — spin up Linode VMs (lite/compose) and/or an LKE cluster (helm) that will host the release validation. Runs in parallel with stage 3 (local development). Use when the user says things like 'provision VMs', 'spin up a compose VM', 'get me a helm cluster', 'start the VMs', 'begin provisioning', or any time a scope exists and infra isn't yet up."
---

## Stage 2 of 9 — provision

Spins up every deployment listed in `scope.deployments.modes` in parallel. Takes ~10 min. **Kick this off early** — stage 3 (local code + test work) runs concurrently while provisioning finishes.

## Command

```bash
export SCOPE=tests3/releases/<id>/scope.yaml
make release-provision SCOPE=$SCOPE
```

### What it does (per mode)

- `lite` → `vm-provision-lite` — Linode VM, install Docker, clone vexa, `make lite` starts a single-container deployment. State → `tests3/.state-lite/`.
- `compose` → `vm-provision-compose` — Linode VM, install Docker, clone vexa, `make all` starts the full docker-compose stack. State → `tests3/.state-compose/`.
- `helm` → `lke-provision + lke-setup` — fresh LKE cluster, installs helm chart from `deploy/helm/charts/vexa`. State → `tests3/.state-helm/`.

All three run in parallel via `make -j`-style backgrounding. Each writes its state dir as it finishes.

## Idempotency

Re-running is safe: if state files already exist for a mode, provisioning reuses the running infra. To force re-provision, destroy first (`make release-teardown SCOPE=$SCOPE`).

## Parallel stage 3

As soon as `release-provision` starts, open another shell and begin stage 3 — edit code + tests + feature README frontmatter. The scope's `proves:` bindings tell you which test steps / checks must go fail→pass once your fix is correct.

## Verify

```bash
for mode in lite compose helm; do
    test -f tests3/.state-$mode/vm_ip     && echo "$mode vm_ip: $(cat tests3/.state-$mode/vm_ip)"
    test -f tests3/.state-helm/lke_node_ip && echo "helm node_ip: $(cat tests3/.state-helm/lke_node_ip)"
done
```

When every mode in `scope.deployments.modes` has an IP written, stage 2 is done.

## Next

Once stage 3 (develop) and stage 2 (provision) are both complete:
→ stage 4: `make release-deploy SCOPE=$SCOPE` (build `:dev`, pull on each VM, restart).
