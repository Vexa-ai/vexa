# Alloy Vexa R&D merge-readiness evidence

**Date:** `2026-07-28`
**Build run:** `alloy-rnd-merge-20260728-090710`
**Runtime resource prefix:** `alloy-rnd-merge-20260728-083416`
**Overall verdict:** `BLOCKED_PRODUCT`
**Merge verdict:** keep the validated commits on the local R&D branch; do not move `main`

## Summary

The opt-in Lite bundled-Python fallback now produces a clean tracked-only image. The final image
starts all five Python services with a Jammy-compatible Python 3.12/OpenSSL 3 runtime, and the
isolated Gateway, Terminal, Agent, and Whisper health endpoints answer HTTP 200.

Real, unpinned-language STT passes for a natural English sample and a natural Russian sample.
It fails the required EN → RU → EN sample: the response contains only the Russian middle leg.
The English source used in that mixed file passes independently, so this is not an empty or
invalid source-audio result.

Inspection of the running `faster-whisper-server` implementation identified the introduction
point. Its file endpoint makes one `WhisperModel.transcribe(..., language=None)` call for the
whole upload. The installed `faster-whisper==1.0.3` documentation and code say that, when language
is absent, language is detected from the first audio window and one detected language is passed
into decoding for the entire file. There is no request option in this server for per-segment
language re-detection. The raw mixed result and server log show one `ru` decision and no English
legs.

The handoff defines a missing language leg as `FAIL` and requires multilingual GREEN before the
Google Meet witness. Therefore the Meet witness was not run, and the changes are not eligible for
fast-forward into `main`.

## Task 0 — host recovery and process ownership

### Expected

Recover enough Windows commit to run Docker without terminating unrelated or unidentified
processes. Preserve the legitimate installed Alloy Meetings runtime.

### Actual

The initial build process tree disappeared after Windows Event 2004 reported virtual-memory
exhaustion. A read-only inventory found ten old persistent Nemotron worker trees from
`F:\alloy.meetings`; several Python workers each held about 8.7 GiB private commit.

Cleanup used exact PID, executable name, creation time, parent/child relationship, and command-line
validation. It terminated 15 owned temporary process trees containing 57 processes. The heavy
Python-worker count changed from 10 to 1. The surviving
`%LOCALAPPDATA%\AlloyMeetings\runtime` process was deliberately preserved.

The detailed machine-readable cleanup record was kept outside Git at:

```text
.superpowers/sdd/tmp/alloy-rnd-merge-20260728-081717/stale-process-cleanup.json
```

### Verdict

`PASS`. Only validated stale temporary trees were stopped. This environment change allowed WSL and
Docker to remain alive for the subsequent bounded builds.

## Task 1 — packaging contract and runtime ABI

### Expected

The exact-`1` `ALLOY_LITE_BUNDLED_PYTHON` path must use one declared Python 3.12 source stage for
all five service venvs. Disabled, invalid, and absent values must keep the upstream path.

### Actual

The first successful clean image used `python:3.12-slim-bullseye`, but live service startup found:

```text
ImportError: libssl.so.1.1
```

Copying Bullseye `/usr/local` into the Jammy runtime was therefore rejected. A focused probe then
copied `python:3.12-slim-bookworm` into the same Jammy target and passed:

```text
import bz2, ctypes, hashlib, sqlite3, ssl
OpenSSL 3.0.2
```

The production source stage was changed to `python:3.12-slim-bookworm`, and a build-time import
guard now executes:

```text
python3.12 -c "import ssl; print('[ALLOY] ' + ssl.OPENSSL_VERSION)"
```

The image declaration, Alloy customization documentation, telemetry design, focused tests, and
merge-readiness design/plan were updated in the same branch. The implementation remains one
build-only boundary reused by all five existing venv blocks. No architecture node or runtime data
flow was added.

Relevant commits:

```text
3965bcc fix(lite): declare bundled Python base image
a694703 fix(lite): align bundled Python runtime ABI
```

### Verdict

`PASS`. The fallback is opt-in, exact-`1`, centrally owned, and independently reversible by setting
`ALLOY_LITE_BUNDLED_PYTHON=0`.

## Task 2 — focused tests and governance

### Expected

All R&D-owned focused contracts must be green before build and again before handoff.

### Actual

Fresh final verification:

| Command | Result |
|---|---|
| `python3 -m unittest deploy/lite/tests/test_lite_bundled_python.py deploy/lite/tests/test_alloy_opt_in.py deploy/lite/tests/test_local_stt_healthcheck.py` under Ubuntu | exit 0; 14 passed in 2.944 s |
| `node scripts/gates.mjs image-licenses` | exit 0; 7 images and 1 bundled component audited |
| `node scripts/gates.mjs lite-makefile` | exit 0 |
| `node scripts/gates.mjs config-contract` | exit 0; 5 services, 198 keys, 8 capabilities |
| `node scripts/gates.mjs runtime-parity` | exit 0 |
| `node scripts/gates.mjs arch-report` | exit 0 |
| `node --test --test-name-pattern="image-licenses" scripts/gates.test.mjs` | exit 0; 7 passed, 0 failed |
| `git diff --check` before evidence commit | exit 0 |

The 14 Python tests cover exact-`1` bundled Python, disabled/invalid/absent flag behavior, one
Makefile build-arg owner, Alloy marking, Jammy/OpenSSL compatibility, upstream opt-in defaults,
and the optional `ALLOY_STT_HEALTHCHECK` command. The healthcheck customization is preserved:
it changes only the third-party Whisper container self-report when enabled and does not classify
real STT reachability by Docker health alone.

### Verdict

`PASS`.

## Task 3 — clean tracked-only image and provenance

### Expected

Produce one unique image from a tracked-only snapshot with no `.git`, `.env`, or `.pnpm-store`;
prove commit/tree/archive hashes, OCI labels, image ID, and all five Python environments.

### Build inputs

| Field | Value |
|---|---|
| Source commit | `a694703b74ae1012810893a5e5f846287482241f` |
| Source tree | `44575827ca62462d5fb049045564c104483d7251` |
| Archive SHA-256 | `25ec297051a4f1f4b681a2101f0649e96e003145779e7db28995963b744ab673` |
| `deploy/lite/Dockerfile.lite` SHA-256 | `111e09dab105b7fc1911c3918b677c39a8f38a904295e049b6e0ec09d184ca5f` |
| `deploy/lite/Makefile` SHA-256 | `50ad9731c462cebe6f2862d58e0e90041d5f2d5fbc3bb436be1d79eb910e4441` |
| `image-licenses.json` SHA-256 | `ccc445d6f1c1b9e659b29031248a70123744c0cae37592302c429b106d4e99ea` |
| Snapshot contained `.git` | no |
| Snapshot contained `.env` | no |
| Snapshot contained `.pnpm-store` | no |

The exact build used `--no-cache --network=host`, these build arguments, and matching OCI labels:

```text
ALLOY_SKIP_HF_CACHE_WARM=1
ALLOY_LITE_BUNDLED_PYTHON=1
ALLOY_LITE_PYTHON_STAGE=alloy-lite-python-1
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=0
org.opencontainers.image.revision=a694703b74ae1012810893a5e5f846287482241f
org.opencontainers.image.ref.name=vexa-lite:alloy-rnd-merge-20260728-090710
```

`--network=host` was the only environmental change from the preceding default-bridge attempt.
The bridge attempt reached `onnxruntime-node` postinstall but held a dead TLS connection to the
NuGet CDN until the 900-second ceiling. WSL host-network access to the same external dependency
was available. No production source was changed to compensate for that local Docker bridge path.

### Build outputs

| Field | Value |
|---|---|
| Start | `2026-07-28T09:08:02+02:00` |
| End | `2026-07-28T09:15:28+02:00` |
| Duration | 7 min 26 s |
| Exit code | 0 |
| Tag | `vexa-lite:alloy-rnd-merge-20260728-090710` |
| Image ID/digest | `sha256:2f33bda8028e9c233952de80d364592ce514019fc254846d9656ffddfec7b4b2` |
| Revision label | `a694703b74ae1012810893a5e5f846287482241f` |
| Ref-name label | `vexa-lite:alloy-rnd-merge-20260728-090710` |
| Build-time Python | 3.12.13 |
| Build-time SSL guard | `[ALLOY] OpenSSL 3.0.2` |

Fresh live imports in the final app container:

```text
admin   OpenSSL 3.0.2 15 Mar 2022
gateway OpenSSL 3.0.2 15 Mar 2022
meeting OpenSSL 3.0.2 15 Mar 2022
runtime OpenSSL 3.0.2 15 Mar 2022
agent   OpenSSL 3.0.2 15 Mar 2022
```

Each line came from its own `/opt/venvs/<service>/bin/python` after importing both `ssl` and
`uvicorn`.

### Verdict

`PASS`. This is a reproducible clean image with proven source and runtime identity.

## Task 4 — isolated Lite runtime

### Expected

Start only uniquely named, non-restarting session resources and prove the final image's public
front doors without changing the four pre-existing Vexa containers.

### Actual

The isolated app ran image
`sha256:2f33bda8028e9c233952de80d364592ce514019fc254846d9656ffddfec7b4b2`.
All five supervised Python services reached `RUNNING`, and Docker reported the app `healthy`.

Fresh endpoint observations:

```text
http://localhost:28056/health 200
http://localhost:23001/        200
http://localhost:28100/health 200
http://localhost:28083/health 200
```

Session resources used the prefix `alloy-rnd-merge-20260728-083416`, unique ports
`28056/23001/28100/28083`, unique network/volumes, and `--restart=no`.

The third-party Whisper container was moved from its isolated bridge to host networking after its
bridge namespace timed out against Hugging Face while the WSL host namespace returned HTTP 200.
The app was not recreated with the resulting host-network Whisper URL because the direct mixed
STT gate failed before application E2E. Consequently, Meeting API → Whisper is not claimed as
proven by this runtime witness.

Pre-existing container identities remained:

```text
c63586f3388a vexa-lite
33b0df45d244 vexa-lite-whisper
0aec47815e81 vexa-lite-minio
70eba0fb5e63 vexa-lite-postgres
```

No lifecycle command targeted these four IDs. Their configured restart policy had previously
started them when the Docker/WSL environment recovered; that external daemon behavior is not
presented as a session-issued start or restart.

### Verdict

`PASS` for image startup and front-door health. Application-level STT is `NOT CHECKED` because the
required direct mixed-language prerequisite failed first.

## Task 5 — real unpinned-language STT

### Expected

Using `Systran/faster-whisper-small`, submit English, Russian, and EN → RU → EN WAV files
sequentially to `/v1/audio/transcriptions`. Every request must contain only:

```text
file=<wav>
model=whisper-1
response_format=verbose_json
```

No request may include `language`. English and Russian control words must be recognizable, and
both languages must appear in the one mixed response.

### Runtime identity

```text
image=fedirz/faster-whisper-server:latest-cpu
model=Systran/faster-whisper-small
device=cpu
faster-whisper-server=editable image source
faster-whisper=1.0.3
ctranslate2=4.4.0
```

The Whisper service ran on host port `28083`. All recorded requests returned HTTP 200.

### Actual

| Sample | Detected | Raw response text | Verdict |
|---|---|---|---|
| English, espeak | `en` (0.99) | `The quick brown fox jumps over the lazy dog.` | `PASS` |
| Russian, Microsoft Irina | `ru` (0.99) | `Сегодня мы проверяем русскую речь в системе распознавания. Эта запись должна быть понятной и правильной.` | `PASS` |
| Mixed Microsoft Zira → Irina → Zira | `ru` (0.49) | `Теперь мы проверяем многоязычную расшифровку на русском языке.` | `FAIL`: both English legs missing |
| English source from the mixed composition, Microsoft Zira | `en` (0.99) | `Today we are checking multilingual speech recognition in English.` | diagnostic `PASS` |

Response-reported audio durations were 3.565 s, 9.068 s, 13.901 s, and 4.304 s respectively.
Server timestamps show each inference completed below the 90-second per-request ceiling. The first
English call included initial model download and completed in about 56 seconds; subsequent
inference calls completed in seconds.

Early espeak Russian/mixed diagnostics were rejected as acceptance evidence because the synthetic
Russian voice was detected as Polish. Changing only the TTS source to the installed natural
Microsoft Irina/Zira voices made the separate Russian and English controls pass. The natural
mixed sample still lost both English legs.

### Root-cause evidence

The running server's `routers/stt.py` calls:

```text
whisper.transcribe(file.file, language=language, ...)
```

once for the entire uploaded file. When the request omits `language`, the configured default is
also `None`. The installed `WhisperModel.transcribe` signature has language-detection controls but
no per-segment multilingual decoding mode. Its implementation detects one language from the first
configured detection window(s), then supplies that single language to the whole transcription
generator. `CreateTranscriptionResponseVerboseJson` likewise exposes one
`transcription_info.language`.

The observed one-language response is therefore consistent with the exact running code path. A
legitimate fix requires a new chunking/per-segment language-selection design or a different STT
engine/contract; neither is a safe configuration toggle within this packaging task.

### Verdict

`FAIL / BLOCKED_PRODUCT`. Automatic EN and automatic RU work in separate requests. Required
within-request code-switch does not.

## Task 6 — Google Meet human-bar witness

### Expected

Run join → audio → STT → Meeting API → Terminal telemetry only after clean image provenance and
direct multilingual STT are both green.

### Actual

Not run. The direct mixed sample failed first.

### Verdict

`BLOCKED_PREREQUISITE`, not a Google Meet product failure and not `BLOCKED_EXTERNAL`. No browser,
login, meeting, bot, token, or user-visible UI action was attempted.

## Task 7 — full and focused final gates

### Full gate

`node scripts/gates.mjs all` ran once from `09:32:51` to `09:41:34` and ended with
`❌ gates failed`. The background launcher preserved complete stdout/stderr but did not retain the
numeric process exit code, so no numeric exit-code claim is made.

R&D-relevant `gate:image-licenses`, `gate:lite-makefile`, `gate:config-contract`,
`gate:runtime-parity`, and `gate:arch-report` were green inside the full run.

Remaining failures:

- `gate:db-schema`: Windows could not resolve the `python3` command;
- `db-budget`: baseline names `admin-api` and `meeting-api` do not match detected DB-engine owners;
- `core/agent` pytest: 479 passed, 37 failed, dominated by Windows symlink/path behavior and
  `Filename too long`;
- turbo build/test: `@vexa/capture-codec#build` failed while pnpm performed a nested install;
- replay/telemetry/eval-baseline downstream lanes remained red after the Node build failure.

Docker compose gates skipped because the Windows process environment did not expose a `docker`
executable; Docker runtime evidence was executed explicitly through Ubuntu/WSL.

### Focused gate

The fresh focused commands and counts are recorded in Task 2. Every R&D-owned focused check passed.

### Verdict

Full gate `RED`; focused Lite change `PASS`. Focused green is not reported as an official full-gate
pass.

## Integration and cleanup decision

The validated commits may be fast-forwarded into the existing local
`alloy/vexa-rnd-runtime-next-20260728` branch so this session branch/worktree can be removed.
`main` must remain unchanged because:

1. required within-request code-switch is red;
2. Google Meet was correctly not run after that prerequisite failed;
3. the full repository gate remains red.

Only resources with the recorded session prefix and the recorded keepalive PID are eligible for
cleanup. The two pre-existing non-session worktrees and their branches must remain unchanged.

No push occurred. No credentials, API keys, tokens, passwords, cookies, or meeting invite secrets
are included in this record.
