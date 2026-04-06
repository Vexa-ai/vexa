---
id: test/vm-deploy
type: validation
requires: []
produces: [VM_URL, GATEWAY_URL, DASH_URL]
validates: [deployment, infrastructure, documentation]
docs: [deploy/compose/README.md, deploy/env/env-example, deploy/compose/Makefile]
mode: manual
---

# VM Deploy — follow the README on a fresh machine

> SSH into a fresh VM and follow deploy/compose/README.md literally.
> Every wall you hit is a documentation gap. Fix the doc, then continue.

VM: Linode g6-standard-6 (6 vCPU, 16 GB RAM), Ubuntu 24.04, `vexa-test-yellow` (172.236.96.198)

## Steps

### S1. Clone and make all

README says:
```
git clone https://github.com/Vexa-ai/vexa.git
cd vexa/deploy/compose
make all
```

**Finding 1: `make` not installed on fresh Ubuntu 24.04.**
README had no prerequisites section. Fixed: added Prerequisites section with `apt-get install -y make` and Docker install.

**Finding 2: `make all` from repo root didn't work (no root Makefile).**
README originally said "From repo root: make all" but Makefile is in `deploy/compose/`. Fixed: changed Quick start to `cd vexa/deploy/compose && make all`.

**Finding 3: Docker not installed on fresh VM.**
`make all` → `check_docker` fails with "Docker is not running". Fixed: added `curl -fsSL https://get.docker.com | sh` to Prerequisites.

### S2. Build fails

After installing make + Docker, `make all` ran `make build` which failed:

**Finding 4: `build-lite` fails — missing files.**
`Dockerfile.lite` tries to COPY `tests/testdata/test-speech-en.wav` and `packages/transcript-rendering/dist/` which don't exist in the `clean` branch. Build aborts.

**Fix:** Changed `make all` to skip build — pulls pre-built `:dev` images from DockerHub instead. Added `make all-build` for building from source.

### S3. Pre-built images path

**Finding 5: Most `:dev` images were not on DockerHub.**
Only `admin-api:dev` and `api-gateway:dev` existed. The rest failed with "pull access denied" or "not found".

**Fix:** Ran `make publish` from local machine to push all images. All 11 images now on DockerHub as `:dev`.

**Finding 6: `make up` defaulted to timestamp tag when no `.last-tag` existed.**
On a fresh clone there's no `.last-tag`, so it tried `IMAGE_TAG=0.10.0-260406-1511` which doesn't exist on DockerHub.

**Fix:** Changed `make up` to default to `IMAGE_TAG=dev` when no `.last-tag` found.

### S4. Continue deployment on VM

(in progress)

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| `make: command not found` | Fresh Ubuntu has no `make` | Added Prerequisites section to README | README must list OS-level deps |
| `No rule to make target 'all'` | Makefile in `deploy/compose/`, not repo root | Changed Quick start to `cd vexa/deploy/compose` | README commands must be copy-pasteable from the stated directory |
| `Docker is not running` | Docker not installed on fresh VM | Added Docker install to Prerequisites | Never assume Docker exists |
| `build-lite` COPY fails | `test-speech-en.wav`, `transcript-rendering/dist/` missing in clean branch | Changed `make all` to pull instead of build | Default path must work from a clean clone |
| `:dev` images not on DockerHub | Images never published | Ran `make publish` | Pre-built path requires published images |
| `up` uses timestamp tag on fresh clone | No `.last-tag` → falls back to generated timestamp | Default to `IMAGE_TAG=dev` when no `.last-tag` | Fresh clone must pull something that exists |
