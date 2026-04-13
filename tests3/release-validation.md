# Release Validation

## Branch model

- **`main`**: stable, `IMAGE_TAG=latest`. Always matches `:latest` on DockerHub.
- **`dev`**: active development, `IMAGE_TAG=dev`. Builds publish to `:dev`.

**Important**: merging dev → main will overwrite `IMAGE_TAG=dev` into env-example. The `ENV_EXAMPLE_LATEST_ON_MAIN` static lock catches this — fix it immediately after merge.

## Release cycle

### 1. Build + publish (on dev branch)

```bash
git checkout dev
make release-build
```

Builds all images with a fresh timestamp tag (e.g. `0.10.0-260413-1504`) and pushes to DockerHub. Tags each image as `:dev` with identical SHA (uses `docker tag + push`, not `imagetools create`).

### 2. VM test

```bash
make release-test
```

Provisions two fresh Linode VMs in parallel. Deploys using the exact user path:
- **Lite VM**: `make lite` (single container + postgres)
- **Compose VM**: `make all` (full multi-service stack)

Runs automated smoke suite (docs, static, env, health, contracts). VMs stay running for human validation.

### 3. Human validation

**Lite VM** (dashboard on port 3000):
- [ ] Dashboard loads, login works
- [ ] API docs page renders
- [ ] Create a browser session — works (process backend, no Docker needed)
- [ ] Create a bot via API — bot starts as child process
- [ ] Bot joins a Google Meet, audio is captured
- [ ] Transcript segments appear in the API
- [ ] Bot stops cleanly
- [ ] No errors in logs: `docker logs vexa 2>&1 | grep -i error | tail -20`

**Compose VM** (dashboard on port 3001):
- [ ] Dashboard loads, login works
- [ ] API docs page renders
- [ ] Create a bot via API — bot container starts
- [ ] Bot joins a Google Meet, audio is captured
- [ ] Transcript segments appear in the API
- [ ] Bot stops cleanly, container removed
- [ ] No errors in logs: `cd /root/vexa && docker compose -f deploy/compose/docker-compose.yml logs --tail=50 2>&1 | grep -i error`

Optional — run full meeting test on VM:
```bash
ssh root@<VM_IP>
cd /root/vexa && make -C tests3 meeting-tts
```

### 4. Validate + destroy VMs

```bash
make release-validate
```

Pushes a `release/vm-validated` commit status to GitHub on the current HEAD and destroys both VMs. This status is required by branch protection to merge to main.

### 5. Open PR dev → main

```bash
gh pr create --base main --head dev --title "Release $(cat VERSION)-$(cat deploy/compose/.last-tag)"
```

The PR requires the `release/vm-validated` status check to pass. If you push new commits, the status resets — must re-validate.

### 6. Merge + fix env-example + promote

After PR is merged:

```bash
git checkout main && git pull

# Fix env-example (merge overwrites IMAGE_TAG to dev)
sed -i 's/IMAGE_TAG=dev/IMAGE_TAG=latest/' deploy/env-example
sed -i 's/BROWSER_IMAGE=vexaai\/vexa-bot:dev/BROWSER_IMAGE=vexaai\/vexa-bot:latest/' deploy/env-example
git add deploy/env-example && git commit -m "fix: restore IMAGE_TAG=latest on main after dev merge"
git push origin main

# Promote to :latest (identical SHA as build tag)
make release-promote
```

### 7. Verify tag propagation

```bash
make -C tests3 locks   # ENV_EXAMPLE_LATEST_ON_MAIN must pass
```

All tags (build, dev, latest) should have identical SHA:
```bash
docker buildx imagetools inspect vexaai/vexa-lite:latest 2>&1 | grep '^Digest:'
docker buildx imagetools inspect vexaai/vexa-lite:0.10.0-YYMMDD-HHMM 2>&1 | grep '^Digest:'
```

## GitHub gate

Branch protection on `main` requires `release/vm-validated` status. This status is per-commit — new commits reset it. Only `make release-validate` can set it.

`enforce_admins` is off — repo admins can bypass (direct push shows "Bypassed rule violations" warning).

## Static regression locks (24 checks)

Run `make -C tests3 locks` to verify. Key release-related locks:
- `ENV_EXAMPLE_LATEST_ON_MAIN` — IMAGE_TAG=latest in env-example
- `BROWSER_IMAGE_IN_ENV` — BROWSER_IMAGE set explicitly
- `NO_IMAGETOOLS_CREATE` — docker tag+push, not imagetools create
- `NO_DEV_FALLBACK_COMPOSE` — no silent :-dev fallbacks
- `NO_NESTED_COMPOSE_VARS` — no nested ${} in docker-compose
- `NO_EXPORT_IMAGE_TAG` — no Make-level export IMAGE_TAG
- `LITE_RECORDING_ENABLED` — recording on in lite
- `LITE_PROCESS_BACKEND` — process backend in lite
- `GATEWAY_TIMEOUT_ADEQUATE` — gateway timeout >= 30s
- `VM_LITE_USES_MAKE` — VM setup uses make lite

## VM access

```bash
# Get IPs
cat tests3/.state-lite/vm_ip
cat tests3/.state-compose/vm_ip

# SSH
ssh root@$(cat tests3/.state-lite/vm_ip)
ssh root@$(cat tests3/.state-compose/vm_ip)

# Destroy manually
make -C tests3 vm-destroy STATE=$(pwd)/tests3/.state-lite
make -C tests3 vm-destroy STATE=$(pwd)/tests3/.state-compose
```
