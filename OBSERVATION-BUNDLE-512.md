# Observation bundle: #512 · browser-session Chromium fails to launch when MinIO env is set (0.10 Lite)

**Issue:** [Vexa-ai/vexa#512](https://github.com/Vexa-ai/vexa/issues/512), the probe successor of
[#461](https://github.com/Vexa-ai/vexa/issues/461) (reporter: Ludv1c0, filed 2026-06-24).
**Slice:** s1 (component C1: reproduce and attribute). The repo has no code change. This file is the only artefact.
**Base:** `31122f96` (v0.12.29). **Observed:** 2026-10-30, 16:45–16:56 UTC, build host (Docker 29.9.2, 8 vCPU, 31 GiB).

> **The deployment under test is NOT built from this repository.** It is a published 0.10-lineage
> image whose browser-session sources are absent from the v0.12 tree. Every claim below about that
> image comes from the image itself (`docker run`, `docker exec`, its shipped `dist/` files).

Facts first. My reading is labelled and comes at the end.

---

## Verdict

| # | Observation | Verdict |
|---|---|---|
| **E1** | Two-run fixture on a pinned 0.10 image: with `MINIO_*`, no Chromium process at T+60s; without it (the control), Chromium is up | **REPRODUCED** (red/green pair below) |
| **E2** | Failing step named | **Candidate (a), in a variant:** the `aws` CLI *is* present but crashes on every invocation (`KeyError: 'opsworkscm'`, exit 1). The browser-session bootstrap runs it synchronously before `launchPersistentContext`, so the session process dies before Chromium. **(b) empty-bucket restore: refuted.** **(c) restore blocks Chromium while Xvfb is up: the observed shape, but the restore *throws* rather than blocks** |
| **Teams-login leg** | Login + Save Storage + authenticated Teams join | **OPEN** for the #461 reporter (no Teams account in this Run) |

---

## 1 · Which image: digests compared

The digest the Design recorded for `vexaai/vexa-lite:latest`, `sha256:216652b4…155edb`, still
resolves today. **It is not the image the reporter ran.** Docker Hub tag history:

| Tag | Index digest | Pushed | Lineage |
|---|---|---|---|
| `latest` = `v0.12.0` = `0.12.0` | `sha256:216652b4d0ec12ef31028a881393b0b3574fe7bdc8ddf6ca2289c90bbf155edb` | 2026-07-10 | **0.12** (`/app/package.json` `"version": "0.12.0"`, `core/meetings/...` layout, no `aws` on PATH: `command -v aws` → rc 127) |
| `0.10.6.3-260610-2224` = `dev` | `sha256:dbd137c076ee05e3b343742cdc0c52b685e7675f05a726554cc999c7ca8b1a39` | 2026-06-10 | **0.10** (`NEXT_PUBLIC_VEXA_OSS_VERSION=0.10.6.3`, `/app/api-gateway/main.py:106 version="1.5.0"`) |

`:latest` moved to 0.12.0 on 2026-07-10, after #461 was filed. The reporter quotes "API version
1.5.0", and the most recent 0.10-lineage push before the report is `0.10.6.3-260610-2224`, so the
probe pins **`vexaai/vexa-lite@sha256:dbd137c076ee05e3b343742cdc0c52b685e7675f05a726554cc999c7ca8b1a39`**
(linux/amd64 image id `dbd137c076ee`, created 2026-06-10T19:27:00Z).
*Caveat:* nothing in the report proves that this is byte-for-byte the digest the reporter pulled. It is the
`:latest` candidate by date and API version. Other 0.10.6.x pushes were not probed.

## 2 · Compose file (the reporter's #461 setup, made deterministic)

```yaml
services:
  vexa:
    image: docker.io/vexaai/vexa-lite@sha256:dbd137c076ee05e3b343742cdc0c52b685e7675f05a726554cc999c7ca8b1a39
    shm_size: 2gb
    env_file: [run.env]          # with.env (reporter's MINIO_* block) or without.env (control)
    environment:
      DATABASE_URL: postgresql://postgres:postgres@postgres:5432/vexa
      ADMIN_API_TOKEN: probe-admin-512
      TRANSCRIPTION_SERVICE_URL: http://unused-stt:8000/v1/audio/transcriptions
      SKIP_TRANSCRIPTION_CHECK: "true"   # image-supported switch; entrypoint otherwise exits 6 without an STT
      STORAGE_BACKEND: "local"   # reporter's value
    depends_on:
      postgres: {condition: service_healthy}
      minio: {condition: service_started}
  postgres:
    image: postgres:15
    environment: {POSTGRES_PASSWORD: postgres, POSTGRES_DB: vexa}
    healthcheck: {test: ["CMD", "pg_isready", "-U", "postgres"], interval: 2s, retries: 30}
  minio:
    image: versity/versitygw:v1.8.0@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499
    entrypoint: ["/bin/sh", "-c", "mkdir -p /data/vexa-sessions /data/vexa-recordings && exec versitygw --port :9000 posix /data"]
    environment: {ROOT_ACCESS_KEY: vexa, ROOT_SECRET_KEY: minio-vexa-2026}
```

`with.env` is the reporter's block verbatim: `MINIO_ENDPOINT=minio:9000`, `MINIO_ACCESS_KEY=vexa`,
`MINIO_SECRET_KEY=minio-vexa-2026`. `without.env` is empty, so the image default `MINIO_ENDPOINT=` (empty) applies.

**Deltas from #461, each irrelevant to the failing step (see §4):**
- STT is omitted, and the entrypoint's STT probe is skipped with the image's own `SKIP_TRANSCRIPTION_CHECK`.
- The S3 sidecar is the repo's pinned versitygw fixture image, under the reporter's hostname and credentials.
  `minio/minio` is no longer anonymously pullable: Docker Hub returned "pull access denied" and quay.io returned 401,
  both on 2026-10-30. Both buckets exist and are empty: `vexa-sessions`, which the reporter created, and
  `vexa-recordings`, the image's `MINIO_BUCKET` default, which the restore actually targets.

Driver per run (`probe.sh with|without`): `compose down -v && up -d`, wait for admin-api, create a user plus an API key
(`/admin/users`, `/admin/users/{id}/tokens?scopes=bot,tx`), then `POST /bots {"mode":"browser_session"}`. At **T+60s**:
`ps`, logs, `/bots/status`, and a framebuffer grab of `:99` (PIL `ImageGrab.grab(xdisplay=":99")` inside the container).

## 3 · The two runs

### Run A: with `MINIO_*` (red)

```
== T0 2026-10-30T16:45:08Z POST /bots {mode: browser_session}
{"id":1,...,"platform":"browser_session","status":"active","bot_container_id":"browser-session-1-153a9e65",...}  http=201
== T+60s 2026-10-30T16:46:19Z ps rows
     34       1       01:16 S    Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset
     86       1       01:15 S    x11vnc -display :99 -forever -nopw -shared -localhost -rfbport 5900 -q
    212     173       01:10 S    fluxbox
                                 (no node dist/docker.js row, no chrome row)
== log lines
[s3-sync] S3 sync down: s3://vexa-recordings/users/1/browser-userdata/browser-data → /tmp/browser-data/
  File "/usr/lib/python3/dist-packages/awscli/customizations/opsworkscm.py", line 21, in alias_opsworks_cm
    alias_command(command_table, 'opsworkscm', 'opsworks-cm')
KeyError: 'opsworkscm'
Error running browser session: Error: Command failed: aws s3 sync "s3://vexa-recordings/users/1/browser-userdata/browser-data" "/tmp/browser-data/" --endpoint-url "http://minio:9000"  --exclude "*/Cache/*" ...
    at s3Sync (/app/vexa-bot/dist/s3-sync.js:35:34)
    at syncBrowserDataFromS3 (/app/vexa-bot/dist/s3-sync.js:38:5)
    at runBrowserSession (/app/vexa-bot/dist/browser-session.js:127:41)
[entrypoint] node dist/docker.js exited with code 1 — keeping container alive for VNC access
== bot status via API
{"running_bots":[{"container_id":"173","container_name":"browser-session-1-153a9e65","platform":"browser_session",
  "status":"running","normalized_status":"Up",...}]}
== framebuffer grab :99
fb (1920, 1080) nonblack_ratio=0.9783      fb-with.png sha256=eb936abbed685fc7014c37cd678a46748e484eafd5fdd1b1bd7d7f0df512c044
```

The probe also printed `chromium_procs=1`. That count is `pgrep -c -f "chrom(e|ium)"` matching its own
parent `sh -c` command line. The `ps` rows above show no chrome process.
**Framebuffer (viewed):** the fluxbox desktop with only the xmessage *"Fbsetbg: I can't find an app to
set the wallpaper…"* dialog (the "wallpaper popup" from #461) and the fluxbox toolbar. **No Chromium window.**

### Run B: without `MINIO_*` (green control)

```
== T0 2026-10-30T16:46:32Z POST /bots {mode: browser_session}   http=201
== T+60s 2026-10-30T16:47:43Z ps rows
     34       1       01:15 S    Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset
    219     180       01:09 S    fluxbox
    248     180       01:09 Sl   node dist/docker.js
    355     248       01:08 Ssl  /ms-playwright/chromium-1194/chrome-linux/chrome --disable-field-trial-config ...
    437     363       01:06 SNl  /ms-playwright/chromium-1194/chrome-linux/chrome --type=renderer ... --user-data-dir=/tmp/browser-data ...
    480     362       01:06 Sl   /ms-playwright/chromium-1194/chrome-linux/chrome --type=gpu-process ...
chromium_procs=11
== log lines
[browser-session] Browser session ready. VNC :6080, CDP :9222
[browser-session] Listening for commands on Redis channels: browser_session:default, bot_commands:meeting:1
== framebuffer grab :99
fb (1920, 1080) nonblack_ratio=1.0000      fb-without.png sha256=340b68cbaf23b16d55ec0b7bebdce0004a020542bbc50e15228b38c277b293c9
```

**Framebuffer (viewed):** a Chromium window on `about:blank` with the "unsupported command-line flag" and
"Google API keys are missing" infobars. The fluxbox toolbar reads "about:blank – Chromium".

The red/green pair differs in exactly one input (`MINIO_*`) and reproduces #461's with-MinIO symptom.
VNC connects, the xmessage popup shows, there is no Chromium, and the API reports the session `running`/`Up`.

## 4 · Attribution against the candidates

| Candidate | Test | Result |
|---|---|---|
| **(a) restore shells out to an `aws` the image lacks** | `command -v aws` in the image | `/usr/bin/aws`, rc 0. **Present** but non-functional: `aws --version` exits **1**. The traceback is the same `KeyError: 'opsworkscm'` raised in `awscli/clidriver.py:_build_command_table`. Cause: Debian `awscli 1.22.34-1` (`/usr/lib/python3/dist-packages/awscli`) imports pip's `botocore 1.43.14` (`/usr/local/lib/python3.10/dist-packages/botocore`). The pair is incompatible, so the CLI dies while building its command table, before any network request. **This is the failing step.** |
| **(b) empty-bucket restore exits nonzero / poisons the profile** | Pre-seed `vexa-recordings/users/1/browser-userdata/browser-data/Local State`, then run the exact `aws s3 sync …` with the session's credentials | `KeyError: 'opsworkscm'`, **exit=1**, same as with an empty bucket. Bucket contents cannot matter because the CLI fails before it reaches S3. **Refuted.** The reporter's proposed workaround (pre-seed an empty session object) would not help. |
| **(c) `MINIO_*` switches bootstrap into a restore that blocks Chromium while Xvfb is up** | ps at T+60s | Xvfb, x11vnc and fluxbox are up, with no `node dist/docker.js` and no chrome. The switch is real: meeting-api adds `userdataS3Path`/`s3Endpoint`/`s3Bucket` to the session config only when `MINIO_ENDPOINT` is non-empty (`/app/meeting-api/meeting_api/meetings.py:798-820`). But the restore **throws, it does not block**: `runBrowserSession` calls `syncBrowserDataFromS3` → `execSync('aws s3 sync …')` (`browser-session.js:127`, `s3-sync.js:35`) with no try/catch before `launchPersistentContext` (`browser-session.js:132`). The rejection reaches `docker.js:112-114` → `process.exit(1)`. |

**Why it is silent.** `/app/vexa-bot/entrypoint.sh:98-102` runs `node dist/docker.js`, logs
`exited with code 1 — keeping container alive for VNC access`, then `wait`s. Xvfb, VNC and websockify stay
up, the runtime keeps reporting the session process `Up`, and the health polls return 200. That matches #461's
"no explicit error, just repeated GET /containers/browser-session-{id} … 200". The only error is in the bot
process's stdout.

**The reporter's other two symptoms (code-read in the same image, not run live here):**
- *Without `MINIO_*`, "Save Storage" persists nothing.* `syncBrowserDataToS3` returns immediately when
  `userdataS3Path`/`s3Endpoint`/`s3Bucket` are absent (`s3-sync.js`, first line of the function). Without
  `MINIO_ENDPOINT` the session config carries none of them, so the save has nowhere durable to go.
- *An `"authenticated": true` bot dies at `requested`.* For `req.authenticated`, meeting-api always sets
  `userdataS3Path` and an `s3Endpoint` (`meetings.py:1116-1126`; with the image's empty `MINIO_ENDPOINT=`
  that endpoint is `http://`, which is still truthy). The bot then calls `syncBrowserDataFromS3` before
  `launchPersistentContext` (`/app/vexa-bot/dist/index.js:2152-2158`), which is the same broken `aws` and so
  the same throw before any browser. That is consistent with `exit_code=1`, `failure_stage=requested`.
  *Not observed live:* this Run spawned no authenticated meeting bot.

## 5 · v0.12 tree (this repo): the same class, different shape

- `deploy/lite/Dockerfile.lite`: `grep -c aws` → **0**. The image built from this tree has no `aws`, so the
  v0.12 restore fails typed as `SessionSyncError('session-restore', …)` / "aws CLI not found on PATH". That
  behaviour is pinned by the existing `core/meetings/modules/remote-browser/src/session-store.test.ts` §3/§3b,
  run in this slice (see the PR's test evidence).
- `core/meetings/services/bot/Dockerfile` installs awscli v2 and asserts `aws --version` at build. That
  assertion is exactly the gate whose absence let the 0.10 Lite image ship a CLI that cannot run.
- `vexaai/vexa-lite:latest` (0.12.0) carries no `aws`, and its gateway mounts no `/b/*` route. The browser
  session UI the reporter used does not exist there.

## 6 · Reading (mine, labelled)

1. **Mechanism:** the published 0.10.6.3 Lite image ships a broken `aws` CLI (Debian awscli v1 shadowed by
   an incompatible pip botocore). Any code path that restores browser userdata from S3 dies at that call.
   `MINIO_*` on a browser session is what turns the restore on. The bootstrap treats the optional restore as
   fatal, and the entrypoint's keep-alive hides the death. This is the "optional capability whose init failure
   is fatal and silent" class that the issue's D7 predicted, plus a packaging defect: no build-time
   `aws --version` assertion in the Lite image.
2. **Workaround for 0.10 Lite users:** leave `MINIO_*` unset for browser sessions, which gives a working but
   non-persistent session. Pre-seeding the bucket does **not** help. On 0.10 Lite, authenticated bots cannot
   work at all because they always call the broken CLI. The docs slice (s2) should say "not available" for
   that lineage, not "pre-seed".
3. **Fix seed (for a later issue, not this Task):** restore is best-effort and launch is never gated on it.
   The image pipeline gets an `aws --version` (or SDK) assertion plus an empty-bucket browser-session smoke
   that must reach "Browser session ready".

## 7 · What was not checked

- Whether the reporter's pull was this exact digest (see §1 caveat). Other 0.10.6.x tags were not probed.
- Real MinIO: it is not pullable anonymously. versitygw stands in, which cannot affect a CLI that fails before
  any request.
- The Teams-login leg (log in at `/b/{token}`, Save Storage, authenticated Teams join): **open for the #461
  reporter**, the preferred signer. With `MINIO_*` unset, the session should launch, as in Run B. With it set,
  it should fail as in Run A.
- Framebuffer PNGs are not committed (bundle-only slice). Their sha256 values are recorded above, and they can be
  regenerated with the compose file and driver.
