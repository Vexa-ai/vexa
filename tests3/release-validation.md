# Release Validation

## Branch model

- **`main`**: stable, `IMAGE_TAG=latest`. Always matches `:latest` on DockerHub.
- **`dev`**: active development, `IMAGE_TAG=dev`. Builds publish to `:dev`.

## Release cycle

### 1. Build + publish (on dev branch)

```bash
make release-build
```

Builds all images with a fresh timestamp tag and pushes to DockerHub. Updates `:dev` pointers.

### 2. VM test

```bash
make release-test
```

Provisions two fresh Linode VMs in parallel. Deploys lite on one, compose on the other. Runs automated smoke suite (docs, static, env, health, contracts). VMs stay running for human validation.

### 3. Human validation

**Lite VM** (dashboard on port 3000):
- [ ] Dashboard loads, login works
- [ ] API docs page renders
- [ ] Create a bot via API — bot container starts
- [ ] Bot joins a Google Meet, audio is captured
- [ ] Transcript segments appear in the API
- [ ] Bot stops cleanly, container removed
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

### 6. Merge + promote

After PR is merged:

```bash
git checkout main && git pull
make release-promote
```

Re-points `:latest` for all images to the validated build tag.

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
