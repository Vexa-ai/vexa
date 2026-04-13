# Release Validation

Full release workflow: build, publish, VM test, human validation, promote to `:latest`.

## 1. Build all images

```bash
make -C deploy/compose build
```

Builds all images (api-gateway, admin-api, runtime-api, meeting-api, agent-api, mcp, dashboard, tts-service, vexa-lite, vexa-bot) with a fresh timestamp tag. Tag saved to `deploy/compose/.last-tag`.

## 2. Publish to DockerHub

```bash
make -C deploy/compose publish
```

Pushes all images and updates `:dev` pointers. Must complete before VM tests (VMs pull `:dev` from DockerHub).

## 3. VM tests — lite + compose in parallel

Use separate STATE directories so both VMs can run simultaneously:

```bash
make -C tests3 vm-lite STATE=$(pwd)/tests3/.state-lite &
make -C tests3 vm-compose STATE=$(pwd)/tests3/.state-compose &
wait
```

### Automated tests run on each VM

**Lite**: smoke, dashboard-auth, containers
**Compose**: smoke, dashboard-auth, dashboard-proxy, containers, webhooks

VMs stay running after automated tests pass.

### VM access

**Lite VM**:
- Dashboard: `http://<LITE_VM_IP>:3000`
- API docs: `http://<LITE_VM_IP>:8056/docs`
- SSH: `ssh root@<LITE_VM_IP>`
- Get IP: `cat tests3/.state-lite/vm_ip`

**Compose VM**:
- Dashboard: `http://<COMPOSE_VM_IP>:3001`
- API docs: `http://<COMPOSE_VM_IP>:8056/docs`
- SSH: `ssh root@<COMPOSE_VM_IP>`
- Get IP: `cat tests3/.state-compose/vm_ip`

## 4. Human validation

### Lite VM

- [ ] Dashboard loads, login works
- [ ] API docs page renders
- [ ] Create a bot via API or dashboard — bot container starts
- [ ] Bot joins a Google Meet, audio is captured
- [ ] Transcript segments appear in the API
- [ ] Bot stops cleanly, container removed
- [ ] No errors in logs: `docker logs vexa 2>&1 | grep -i error | tail -20`

### Compose VM

- [ ] Dashboard loads, login works
- [ ] API docs page renders
- [ ] Create a bot via API or dashboard — bot container starts
- [ ] Bot joins a Google Meet, audio is captured
- [ ] Transcript segments appear in the API
- [ ] Bot stops cleanly, container removed
- [ ] No errors in service logs: `cd /root/vexa && docker compose -f deploy/compose/docker-compose.yml logs --tail=50 2>&1 | grep -i error`

### Optional: run meeting-tts on VM

```bash
# SSH into either VM, then:
cd /root/vexa
make -C tests3 meeting-tts
```

## 5. Destroy VMs

```bash
make -C tests3 vm-destroy STATE=$(pwd)/tests3/.state-lite
make -C tests3 vm-destroy STATE=$(pwd)/tests3/.state-compose
```

## 6. Promote to `:latest`

```bash
make -C deploy/compose promote-latest
```

Re-points `:latest` for all images to the build tag from `deploy/compose/.last-tag`.

Verify: `cat deploy/compose/.last-tag` shows the promoted tag.
