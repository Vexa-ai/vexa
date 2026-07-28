# Alloy Vexa R&D Merge Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a provenance-bound Vexa Lite build, prove multilingual Whisper and the Google
Meet path, then fast-forward the verified R&D line into `main` without touching unrelated work.

**Architecture:** Fix the single packaging-policy defect in `image-licenses.json`; preserve the
existing exact-`1` Alloy Python bootstrap and all flag-off behavior. Generate build and live
evidence from an immutable tracked-only snapshot, run the tested image in isolated containers,
then integrate only through verified fast-forwards.

**Tech Stack:** Git worktrees, Node.js gate runner, Python `unittest`, GNU Make, WSL2 Ubuntu,
Docker/BuildKit, Vexa Lite, faster-whisper CPU, PowerShell.

## Global Constraints

- Work only in
  `F:\vexa\.superpowers\sdd\worktrees\alloy-rnd-merge-readiness` until the final fast-forward.
- Do not modify `F:\vexa-alloy-rnd-review-fixes` or `F:\vexa-whisper-healthcheck`.
- Do not stage, commit, reset, stash, clean, or delete user-owned changes.
- Do not push or open a PR.
- Do not stop, restart, rename, remove, or reuse the four pre-existing `vexa-lite*` containers.
- Start at most one session-owned WSL keepalive, record its Windows PID, and terminate only that PID.
- Use a tracked-only source snapshot, a unique image tag, and a 15-minute build limit.
- Build with exact `ALLOY_LITE_BUNDLED_PYTHON=1`; flag-off behavior must remain unchanged.
- Run real English, Russian, and English-to-Russian-to-English samples with
  `Systran/faster-whisper-small` and no request `language` field.
- Classify outcomes only as `PASS`, `FAIL`, `BLOCKED_ENV`, or `BLOCKED_EXTERNAL`.
- Apply DRY and SOLID proportionately: one image-policy owner, no gate special case, no duplicated
  build branch, clear evidence boundaries, low coupling, safe rollback, and focused tests.
- Never add AI attribution to commits, documentation, code, or comments.

---

### Task 1: Declare the opt-in Python base image

**Files:**

- Modify: `image-licenses.json`
- Test: `scripts/gates.test.mjs`

**Interfaces:**

- Consumes: Dockerfile `FROM python:3.12-slim-bullseye` discovered by
  `gate:image-licenses`.
- Produces: one `images[]` entry keyed by `name: "python"` with a Category-A SPDX licence.

- [ ] **Step 1: Reconfirm the focused RED**

Run:

```powershell
node scripts/gates.mjs image-licenses
node --test --test-name-pattern="image-licenses" scripts/gates.test.mjs
```

Expected: the gate and committed-tree vacuity test fail only because
`python:3.12-slim-bullseye` is undeclared; the six adversarial negative controls remain green.

- [ ] **Step 2: Add the minimal manifest entry**

Insert after the existing Playwright base-image entry:

```json
{
  "name": "python",
  "license": "Python-2.0",
  "disposition": "base-image",
  "note": "Pinned Python 3.12 source for the exact ALLOY_LITE_BUNDLED_PYTHON=1 Lite build stage. The flag-off build does not select or copy this stage; Python is distributed under the PSF License Version 2 (SPDX Python-2.0)."
}
```

Do not change `scripts/gates.mjs`, the Dockerfile, or the Makefile.

- [ ] **Step 3: Prove the GREEN and retain adversarial coverage**

Run:

```powershell
node scripts/gates.mjs image-licenses
node --test --test-name-pattern="image-licenses" scripts/gates.test.mjs
```

Expected: gate green; seven image-license tests pass, including all real-gate RED fixtures.

- [ ] **Step 4: Commit only the manifest**

Run:

```powershell
git diff --check
git add -- image-licenses.json
git diff --cached --check
git commit -m "fix(lite): declare bundled Python base image"
```

Expected: one-file commit; clean worktree.

---

### Task 2: Close focused contract and governance evidence

**Files:**

- Verify: `deploy/lite/tests/test_lite_bundled_python.py`
- Verify: `deploy/lite/tests/test_alloy_opt_in.py`
- Verify: `deploy/lite/tests/test_local_stt_healthcheck.py`
- Verify: `scripts/gates.mjs`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/focused-tests.log`

**Interfaces:**

- Consumes: Task 1's green packaging manifest.
- Produces: a focused evidence log that gates the expensive Docker build.

- [ ] **Step 1: Verify Git identity and tracked cleanliness**

Run:

```powershell
git status --porcelain=v1 -b
git rev-parse HEAD
git merge-base --is-ancestor main HEAD
```

Expected: the session branch is clean and `main` is an ancestor.

- [ ] **Step 2: Run the three exact Lite Python suites inside Ubuntu**

Run from the worktree's WSL path:

```bash
python3 -m unittest \
  deploy/lite/tests/test_lite_bundled_python.py \
  deploy/lite/tests/test_alloy_opt_in.py \
  deploy/lite/tests/test_local_stt_healthcheck.py
```

Expected: all bundled-Python, flag-off, and healthcheck assertions pass.

- [ ] **Step 3: Run the focused governance gates**

Run:

```powershell
node scripts/gates.mjs image-licenses
node scripts/gates.mjs lite-makefile
node scripts/gates.mjs config-contract
node scripts/gates.mjs runtime-parity
node scripts/gates.mjs arch-report
```

Expected: all five focused gates pass. Any failure stops the build and is classified before any
retry.

- [ ] **Step 4: Record the exact focused results**

Create a session artifact directory under the ignored path computed as
`Join-Path $worktree ".superpowers\sdd\tmp\$runId"`. Record the command, start/end time, exit code,
pass/fail count, Git revision, and what the command did not check in `focused-tests.log`.

```powershell
$worktree = 'F:\vexa\.superpowers\sdd\worktrees\alloy-rnd-merge-readiness'
$runId = 'alloy-rnd-merge-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$artifactDir = Join-Path $worktree ".superpowers\sdd\tmp\$runId"
New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
```

Expected: the log contains no credentials and remains outside the Git diff.

---

### Task 3: Build one provenance-bound Lite image

**Files:**

- Read: `deploy/lite/Dockerfile.lite`
- Read: `deploy/lite/Makefile`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/source.tar`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/source/`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/build.stdout.log`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/build.stderr.log`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/provenance.json`

**Interfaces:**

- Consumes: the clean Task 1 commit and Task 2 focused PASS.
- Produces: the computed image tag `$imageTag = "vexa-lite:$runId"`, immutable image ID, OCI
  labels, and source hashes.

- [ ] **Step 1: Snapshot only tracked source**

In PowerShell, define one run id and paths, then archive the exact commit:

```powershell
$snapshotDir = Join-Path $artifactDir 'source'
$archivePath = Join-Path $artifactDir 'source.tar'
$sourceCommit = git -C $worktree rev-parse HEAD
$sourceTree = git -C $worktree rev-parse 'HEAD^{tree}'
$imageTag = "vexa-lite:$runId"
New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
git -C $worktree archive --format=tar --output=$archivePath HEAD
tar -xf $archivePath -C $snapshotDir
```

Expected: `git status` is absent from the snapshot, no untracked `.env` or `.pnpm-store` is
present, and the archive SHA-256 is recorded.

- [ ] **Step 2: Inventory WSL/Docker without changing existing containers**

Run:

```powershell
wsl.exe --list --verbose
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('wsl.exe','wslhost.exe','wslservice.exe','vmmemWSL.exe') } |
  Select-Object ProcessId,ParentProcessId,CreationDate,Name,CommandLine
wsl.exe -d Ubuntu -- docker ps --no-trunc
```

Expected: Ubuntu and Docker are reachable; the four pre-existing `vexa-lite*` container IDs,
images, start times, restart counts, and statuses are recorded before the build. Do not run any
container lifecycle command against them.

- [ ] **Step 3: Start and record one keepalive**

Start one hidden process:

```powershell
$keepalive = Start-Process -FilePath wsl.exe `
  -ArgumentList @('-d','Ubuntu','--','bash','-lc','exec sleep infinity') `
  -PassThru -WindowStyle Hidden
$keepalivePid = $keepalive.Id
```

Record the PID, parent PID, command line, and creation time in the build log. Reuse this process
until Docker evidence is complete. Do not start a second keepalive.

- [ ] **Step 4: Run the bounded build**

Convert `$snapshotDir` to a WSL path and invoke:

```powershell
$snapshotWsl = (wsl.exe -d Ubuntu -- wslpath -a $snapshotDir).Trim()
$buildEnv = @("SOURCE_COMMIT=$sourceCommit", "IMAGE_TAG=$imageTag")
$buildScript = @'
set -euo pipefail
cd "$SNAPSHOT"
timeout --signal=TERM --kill-after=30s 900s docker build \
  --progress=plain \
  --build-arg ALLOY_SKIP_HF_CACHE_WARM=1 \
  --build-arg ALLOY_LITE_BUNDLED_PYTHON=1 \
  --build-arg ALLOY_LITE_PYTHON_STAGE=alloy-lite-python-1 \
  --build-arg NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=0 \
  --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
  --label "org.opencontainers.image.ref.name=$IMAGE_TAG" \
  -f deploy/lite/Dockerfile.lite \
  -t "$IMAGE_TAG" .
'@
wsl.exe -d Ubuntu -- env "SNAPSHOT=$snapshotWsl" $buildEnv bash -lc $buildScript
```

Run it from the tracked-only snapshot. Redirect complete stdout and stderr to the two build logs
and report a short tail at least once per minute. Expected: exit `0` within 15 minutes and all five
service venv steps complete.

- [ ] **Step 5: Verify artifact identity and source hashes**

Inspect the image:

```bash
docker image inspect "$IMAGE_TAG" \
  --format '{{json .Id}} {{json .RepoDigests}} {{json .Config.Labels}}'
```

Record:

- image ID and any RepoDigest;
- `org.opencontainers.image.revision == $sourceCommit`;
- `org.opencontainers.image.ref.name == $imageTag`;
- SHA-256 for `source.tar`, `deploy/lite/Dockerfile.lite`, `deploy/lite/Makefile`, and
  `image-licenses.json`;
- the five service-venv completion lines from the build log.

Expected: every value exists and matches the tracked snapshot. Otherwise classify `FAIL` or
`BLOCKED_ENV` and stop.

---

### Task 4: Prove multilingual Whisper on an isolated stack

**Files:**

- Read: `deploy/lite/Makefile`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/multilingual/`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/container-inventory.log`

**Interfaces:**

- Consumes: Task 3 image, unique run id, image tag, and known test credentials.
- Produces: three real audio responses without a request-level language pin and an isolated Lite
  stack for the Google Meet witness.

- [ ] **Step 1: Reserve isolated names and ports**

Derive the names from the already recorded `$runId`:

```powershell
$network = "$runId-net"
$pg = "$runId-postgres"
$minio = "$runId-minio"
$whisper = "$runId-whisper"
$app = "$runId-app"
$pgVolume = "$runId-pgdata"
$minioVolume = "$runId-miniodata"
$gatewayPort = 28056
$terminalPort = 23001
$agentPort = 28100
$sttPort = 28083
```

Before creation, prove that none of these names, volumes, or host ports already exists. If a port
is occupied, choose a free port in `28056..28999`, record it once, and use it consistently.
Every session container uses `--restart=no`.

- [ ] **Step 2: Start isolated PostgreSQL, MinIO, and Whisper**

Create the unique network and volumes, then export the PowerShell values into one Ubuntu shell:

```powershell
$runtimeEnv = @(
  "NETWORK=$network", "PG=$pg", "MINIO=$minio", "WHISPER=$whisper", "APP=$app",
  "PG_VOLUME=$pgVolume", "MINIO_VOLUME=$minioVolume", "STT_PORT=$sttPort",
  "GATEWAY_PORT=$gatewayPort", "TERMINAL_PORT=$terminalPort", "AGENT_PORT=$agentPort",
  "IMAGE_TAG=$imageTag"
)
```

Run the following script through
`wsl.exe -d Ubuntu -- env $runtimeEnv bash -lc $runtimeScript`:

```bash
docker run -d --name "$PG" --network "$NETWORK" --restart=no \
  -e POSTGRES_DB=vexa -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e TZ=UTC -e PGTZ=UTC -v "$PG_VOLUME:/var/lib/postgresql/data" \
  postgres:17-alpine -c idle_in_transaction_session_timeout=60000

docker run -d --name "$MINIO" --network "$NETWORK" --restart=no \
  -e MINIO_ROOT_USER=vexa-access-key -e MINIO_ROOT_PASSWORD=vexa-secret-key \
  -v "$MINIO_VOLUME:/data" minio/minio:latest server /data --console-address :9001

docker run -d --name "$WHISPER" --network "$NETWORK" --restart=no \
  --health-cmd="python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\", timeout=2).read()'" \
  --health-interval=5s --health-timeout=3s --health-retries=60 \
  -e WHISPER__MODEL=Systran/faster-whisper-small \
  -e WHISPER__INFERENCE_DEVICE=cpu -e WHISPER__TTL=-1 \
  -p "$STT_PORT:8000" fedirz/faster-whisper-server:latest-cpu
```

Poll real readiness with a five-minute ceiling. Expected: PostgreSQL accepts `pg_isready`, MinIO
answers its live endpoint, and Whisper `/health` is HTTP 200 with the configured multilingual
model present in container environment.

- [ ] **Step 3: Start the tested Lite image**

Run the image with the unique container, network, and ports; `--restart=no`; known local-only
secrets; the isolated PostgreSQL/MinIO/Whisper endpoints; and:

```text
ALLOY_STT_MAX_CONCURRENCY=1
ALLOY_STT_CHANNEL_BACKPRESSURE=latest
ALLOY_STT_LANGUAGE_MODE=auto
ALLOY_STT_TELEMETRY=1
TRANSCRIPTION_SERVICE_URL=http://$whisper:8000
TRANSCRIPTION_SERVICE_TOKEN=local-vexa
```

Expected: gateway `/health`, Terminal, and Agent front doors answer within three minutes and the
running image ID equals Task 3's image ID.

- [ ] **Step 4: Generate three real audio samples**

In a disposable `debian:stable-slim` container, install `espeak-ng` and `ffmpeg`, then generate
16-kHz mono WAV files:

```text
English: the quick brown fox jumps over the lazy dog
Russian: сегодня мы проверяем русскую речь в системе распознавания
Mixed EN: today we
Mixed RU: проверяем многоязычную расшифровку
Mixed EN: and return to English
```

Concatenate the three mixed segments in that order. Store only WAV files and synthesis logs under
the runtime artifact directory.

- [ ] **Step 5: Submit requests without `language`**

For each WAV, run:

```bash
curl --fail-with-body --max-time 180 \
  http://localhost:$STT_PORT/v1/audio/transcriptions \
  -F file=@sample.wav \
  -F model=whisper-1 \
  -F response_format=verbose_json
```

The argv must contain no `language` form field. Record the complete JSON response and elapsed time.
Expected:

- English response contains recognizable English words from the source sentence;
- Russian response contains recognizable Russian words from the source sentence;
- mixed response contains non-empty recognizable content from English, Russian, then English;
- all three requests return HTTP 2xx and non-empty `text`.

Any empty result, missing language leg, server error, or hidden language pin is `FAIL`.

---

### Task 5: Run the Google Meet human-bar witness

**Files:**

- Read: `deploy/lite/probe.sh`
- Read: `deploy/lite/tests/journey.sh`
- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-rnd-merge-*/meet-evidence/`

**Interfaces:**

- Consumes: the isolated Task 4 app, gateway, Terminal, Whisper, and known admin token.
- Produces: one aligned Meet/API/Redis/bot-log/Terminal evidence bundle or `BLOCKED_EXTERNAL`.

- [ ] **Step 1: Prove machine readiness before visible UI**

Run `deploy/lite/probe.sh` against the unique app container and gateway, overriding
`APP_CONTAINER`, `HOST_GATEWAY_PORT`, `GATEWAY_URL`, and `ADMIN_TOKEN`.

Expected: a token can be minted and authenticated product endpoints respond. Record the token only
in process memory; redact it from logs.

- [ ] **Step 2: Check for a usable signed-in meeting session**

Use the available signed-in browser session to create or open one disposable Google Meet and keep
the meeting active. If no signed-in session exists and the meeting cannot be entered as a guest,
record `BLOCKED_EXTERNAL` with the observed login/admission state and do not classify the product
as failed.

- [ ] **Step 3: Start one bot through the product API**

Extract the native Meet id into `$nativeMeetingId`; keep the minted token only in
`$vexaApiKey`; set `$gatewayUrl = "http://localhost:$gatewayPort"`; then call:

```powershell
$botRequest = @{
  platform = 'google_meet'
  native_meeting_id = $nativeMeetingId
  bot_name = 'Alloy R&D Witness'
  transcribe_enabled = $true
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$gatewayUrl/bots" `
  -Headers @{ 'X-API-Key' = $vexaApiKey } `
  -ContentType 'application/json' -Body $botRequest
```

Expected: HTTP 201, then status advances through requested/joining or awaiting-admission to active.
Admit the bot if the Meet UI requests it.

- [ ] **Step 4: Speak the multilingual sequence and align observations**

Play or speak English, Russian, then English in the meeting. For up to 15 minutes, capture aligned:

- gateway `/meetings` and `/transcripts` responses;
- Redis telemetry key, TTL, active/waiting counts, lag, and recovery to zero;
- bot/app logs for lifecycle and Whisper requests;
- Terminal meeting/transcript/`ALLOY STT` state.

Expected: all three language legs appear in the transcript, the meeting lifecycle is coherent, and
telemetry returns to idle without `STT unavailable` being treated as an empty queue.

- [ ] **Step 5: Stop only the session bot and record the verdict**

Delete the bot through:

```powershell
Invoke-RestMethod -Method Delete `
  -Uri "$gatewayUrl/bots/google_meet/$nativeMeetingId" `
  -Headers @{ 'X-API-Key' = $vexaApiKey }
```

Expected: session meeting reaches a terminal status. Do not remove or restart any pre-existing
container.

---

### Task 6: Record evidence, run the final gate once, and integrate

**Files:**

- Create: `docs/superpowers/evidence/README.md`
- Create: `docs/superpowers/evidence/2026-07-28-alloy-rnd-merge-readiness.md`

**Interfaces:**

- Consumes: all exact commands, timestamps, exit codes, hashes, image identity, live responses, and
  blockers from Tasks 1–5.
- Produces: durable merge evidence and a fast-forwarded local `main`.

- [ ] **Step 1: Write the evidence record**

The record must contain:

- Expected / Actual / Verdict for every task;
- command, duration, exit code, and pass/fail counts;
- source commit/tree/archive hash and image ID/digest/labels;
- source file hashes and five service-venv proof lines;
- pre-existing and session-owned container inventories;
- three request shapes proving no `language` field and their transcript outputs;
- Google Meet/API/Redis/log/Terminal alignment, or the exact `BLOCKED_EXTERNAL` state;
- what was not checked and every remaining limitation;
- explicit statements that no push occurred and no pre-existing container was intentionally
  changed.

Do not include API keys, tokens, passwords, cookies, meeting invite secrets, or other credentials.

- [ ] **Step 2: Run one fresh full gate**

Run once:

```powershell
node scripts/gates.mjs all
```

Use a 15-minute ceiling and preserve complete output. Expected: the R&D-owned image-license
failure is gone. Record all remaining failures exactly and distinguish code-diff failures from
Windows-local baseline/tooling failures. Do not repeat an unchanged deterministic red.

- [ ] **Step 3: Run final focused verification**

Run:

```powershell
node scripts/gates.mjs image-licenses
node --test --test-name-pattern="image-licenses" scripts/gates.test.mjs
git diff --check
git status --short
```

Expected: focused checks pass and only the two evidence documentation files are uncommitted.

- [ ] **Step 4: Commit the evidence files**

Run:

```powershell
git add -- docs/superpowers/evidence/README.md `
  docs/superpowers/evidence/2026-07-28-alloy-rnd-merge-readiness.md
git diff --cached --check
git commit -m "docs: record Alloy R&D merge evidence"
```

Expected: clean session worktree.

- [ ] **Step 5: Verify ancestry and fast-forward the R&D branch**

In `F:\vexa`, first prove:

```powershell
git status --porcelain=v1 -b
git rev-parse HEAD
git merge-base --is-ancestor alloy/vexa-rnd-runtime-next-20260728 `
  alloy/vexa-rnd-merge-readiness-20260728
```

Expected: only the known untracked `.pnpm-store/` is present and the ancestry check succeeds.
Then run:

```powershell
git merge --ff-only alloy/vexa-rnd-merge-readiness-20260728
```

- [ ] **Step 6: Fast-forward `main` and return the primary checkout to it**

Run:

```powershell
git switch main
git merge --ff-only alloy/vexa-rnd-runtime-next-20260728
git status --porcelain=v1 -b
```

Expected: `F:\vexa` is on `main`, the R&D commit is an ancestor of `main`, and `.pnpm-store/`
remains untouched.

- [ ] **Step 7: Clean only session-owned runtime resources**

Stop and remove only containers, network, and volumes named by the recorded `$runId`. Terminate
only `$keepalivePid` if it still exists. Re-inventory the four pre-existing container IDs and report
any externally caused state change without attempting to repair it.

Expected: no session-owned container/network/volume or keepalive remains.

- [ ] **Step 8: Remove only this session's temporary Git objects**

From `F:\vexa`, validate the exact path and ancestry, then:

```powershell
git worktree remove "F:\vexa\.superpowers\sdd\worktrees\alloy-rnd-merge-readiness"
git branch -d alloy/vexa-rnd-merge-readiness-20260728
git branch -d alloy/vexa-rnd-runtime-next-20260728
git worktree list --porcelain
git branch --list
```

Expected: the temporary and integrated R&D branches are gone. The review-fixes and
Whisper-healthcheck worktrees and branches remain exactly as found. Git removes the clean temporary
worktree together with its ignored session artifact directory; the committed redacted evidence
record remains on `main`.
