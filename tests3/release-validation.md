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

### 2. Deployment validation — 3 deployments in parallel

```bash
make release-test
```

Provisions **three fresh deployments in parallel** and runs `validate-<mode>` on each. Every test emits a JSON artifact under `.state/reports/<mode>/<test>.json`:

| Deployment | Path | Infrastructure |
|---|---|---|
| **Lite VM** | `make lite` | single container + postgres (Linode VM) |
| **Compose VM** | `make all` | full multi-service stack (Linode VM) |
| **Helm / LKE** | `lke-provision + lke-setup` | fresh LKE cluster + helm chart |

Each deployment runs the cheap-tier tests registered in `test-registry.yaml` for its mode (`smoke-static`, `smoke-env`, `smoke-health`, `smoke-contract`, `webhooks`, `containers`, `dashboard-auth`, `dashboard-proxy`).

After all three finish, `make release-report` aggregates per-deployment reports into **`tests3/reports/release-<tag>.md`** with per-feature confidence computed from the DoDs declared in each `features/*/README.md` frontmatter. The release gate fails if any feature is below its `gate.confidence_min`.

```bash
# Skip helm (faster, cheaper, but lower coverage):
make release-test-no-helm
```

Deployments stay up after the run for optional human cross-checking. `make release-validate` (step 4) tears them all down.

### 3. Review the report + optional human cross-check

Open **`tests3/reports/release-<tag>.md`** — this is now the gate. It shows:

- Deployment coverage table (tests run / passed / failed per mode).
- Per-feature confidence with gate threshold and pass/fail.
- DoD details per feature with live evidence from each deployment.
- Raw test results per deployment.

The CLI gate runs as part of `release-test`; if any feature is below its `gate.confidence_min`, the build exits non-zero and ship is blocked.

Optional human cross-check (SSH into any VM):

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
- [ ] Webhooks: configure webhook_url via dashboard, create bot, verify `webhook_delivery` in meeting data after completion
- [ ] Webhooks: status change webhooks fire for enabled event types (meeting.started, meeting.completed)
- [ ] No errors in logs: `cd /root/vexa && docker compose -f deploy/compose/docker-compose.yml logs --tail=50 2>&1 | grep -i error`

Optional — run full meeting test on VM:
```bash
ssh root@<VM_IP>
cd /root/vexa && make -C tests3 meeting-tts
```

### 4. Ship

```bash
make release-ship
```

One command that does everything after human validation:
1. Pushes `release/vm-validated` commit status to GitHub
2. Destroys VMs
3. Creates PR dev → main (or merges existing one)
4. Fixes env-example on main (IMAGE_TAG=latest)
5. Promotes all images to `:latest` (same SHA as build tag)

### 5. Verify

```bash
make -C tests3 locks   # all 24 checks must pass on main
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
