---
name: 9-teardown
description: "Invoke AFTER stage 8 (ship) to destroy the provisioned Vexa release infrastructure — Linode VMs and LKE cluster. Use when the user says 'tear down', 'destroy the VMs', 'clean up', 'wind down', 'kill the cluster', 'we're done', or after a successful release-ship."
---

## Stage 9 of 9 — teardown

The release shipped. Destroy the infra that validated it.

## Command

```bash
make release-teardown SCOPE=$SCOPE
```

## What it does

For each mode in `scope.deployments.modes`:

- `lite` → `linode-cli linodes delete <id>` via `tests3/lib/vm.sh destroy`. Idempotent: if the VM is already gone, succeeds quietly.
- `compose` → same `linode-cli linodes delete`.
- `helm` → `linode-cli lke delete <cluster>` via `tests3/lib/lke.sh destroy`. Destroys the entire LKE cluster (nodes + control plane + attached disks).

State directories `tests3/.state-{lite,compose,helm}/` are kept (they contain the IDs that were destroyed — useful for postmortem / audit).

## Prerequisites

- Stage 8 (`release-ship`) completed successfully. Don't destroy infra you haven't validated against.

## Recovery — if teardown fails

If Linode CLI returns an error (e.g. quota / perms / network), the VM might still be running. Verify:

```bash
linode-cli linodes list --format "id,label,status" | grep vexa
linode-cli lke clusters-list --format "id,label,status"
```

Destroy manually:

```bash
linode-cli linodes delete <id>
linode-cli lke cluster-delete <id>
```

## Don't skip teardown

Linode VMs + LKE clusters bill by the hour. A forgotten release test deployment costs $70-100/month. The `release-teardown` command is cheap; forgetting it is not.

## Safety: scope vs. non-scope

If `SCOPE` isn't set, `release-teardown` tears down all three known state dirs (`.state-lite`, `.state-compose`, `.state-helm`) — it's an all-or-nothing cleanup. To only teardown one mode, use the underlying targets directly:

```bash
make -C tests3 vm-destroy STATE=$PWD/tests3/.state-compose
make -C tests3 lke-destroy STATE=$PWD/tests3/.state-helm
```

## After teardown

Optional cleanup:

- Close any release-related GH issues still open.
- Post release summary to Discord #announcements.
- Archive the scope file — it's historical record: `git log tests3/releases/<id>/`.

Ready for the next release cycle — start back at stage 0 (`0-groom`).
