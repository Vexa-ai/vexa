# bot — the meetings ephemeral join+capture worker (Node/TS)

## Purpose

The disposable meeting-joining browser bot (P7 worker). It boots from a single `invocation.v1`
config in `VEXA_BOT_CONFIG`, joins a Google Meet / Zoom / Teams call over a humanized browser,
captures audio, transcribes it (`@vexa/transcribe-whisper`), and publishes confirmed
`transcript.v1` segments + `lifecycle.v1` status — then dies. Node/TS because the join+capture
domain lives in the browser/Playwright ecosystem; it's a modular monolith behind ports — the
orchestrator core is offline-provable, while browser/redis/http are adapters wired only at the
composition root (`src/index.ts`).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | scheduler / meeting-api (spawner) | `invocation.v1` in `VEXA_BOT_CONFIG` env | boot config: meeting URL, platform, ids, callback/upload URLs, secrets |
| spawns-over | `@vexa/join` + `@vexa/remote-browser` | in-process port (`JoinDriver`) | join/leave/removal over a humanized browser page |
| publishes | collector [Py] | redis stream `transcription_segments` (XADD) | `transcript.v1` durable segment feed |
| publishes | gateway → dashboard | redis pub/sub `tc:meeting:{id}:mutable` | live mutable `transcript.v1` segment |
| produces | meeting-api | HTTP POST → `inv.meetingApiCallbackUrl` | `lifecycle.v1` status events (retry/backoff) |
| produces | meeting-api | HTTP POST → `inv.recordingUploadUrl` | assembled recording master (multipart) |
| consumes | userdata store (S3) | `aws` CLI with the invocation's read-only `s3*` pair (`@vexa/remote-browser`), authenticated mode only | before launch, the stored session's [`session-profile.v1`](../../contracts/session-profile.v1) paths, read-only |
| consumes | meeting-api | HTTP GET → `inv.sessionRestoreUrl` (`Authorization: Bearer <token>`), authenticated mode with a saved bot account, before launch | the bot account's profile, a `session-profile.v1` `WritebackBody`; only profile paths are written, one bad entry refuses all of it |
| produces | meeting-api | HTTP PUT → `inv.sessionWritebackUrl` (`Authorization: Bearer <token>`), authenticated mode with a store or a bot account, on clean teardown | the rotated session, a `session-profile.v1` `WritebackBody`; no field ⇒ no write-back |
| consumes | meeting-api (spawner) | `VEXA_BROWSER_SESSION` env (JSON), instead of `VEXA_BOT_CONFIG` | a browser-session workload: `signin` or `verify` of a bot account (below) |
| produces | meeting-api | HTTP PUT → `save_url` (signin) · GET `restore_url` + POST `result_url` (verify), `Authorization: Bearer <token>` | the signed-in profile as a `WritebackBody` · `{logged_in, detail}` |
| serves | meeting-api (live-view relay) | websockify on `:6080` (noVNC web client + VNC WebSocket), signin only | the browser display, behind a per-session VNC password |
| consumes | gateway (commands) | redis pub/sub `bot_commands:meeting:{id}` | `acts.v1` commands (e.g. `speak` / `speak_stop`, `chat_send`, `reconfigure` of the transcription language — see [src/README.md](src/README.md#transcription-language)) |

### Pre-join reachability gate (#530)

Before any meeting navigation, the orchestrator makes the FIRST `joining` emit **load-bearing**
(`orchestrator.ts` → `emitJoining`): the HTTP sink's `emitReachable` reports whether the meeting-api
callback answered at all (any HTTP status = reachable; only an all-attempts network failure — the
fresh-node CNI-lag signature — is `unreachable`). Reachable ⇒ the join proceeds with **zero added
latency** (the secondary channel is never probed). Unreachable ⇒ it probes the secondary channel
(redis PING); **either channel up ⇒ proceed** (the bot can still report), **both down ⇒ refuse to
join** and terminate fast with a typed, attributed outcome instead of an opaque crashloop.

**Exit codes** (the terminal signal on k8s, where each bot is a bare Pod, `--restart=Never`):

| Code | Meaning |
|---|---|
| `0` | clean terminal (`completed`, or a user `stop` withdraw) |
| `1` | join / runtime failure (`join_failure`, `validation_error`, admission rejected/timeout) |
| `3` | **control plane unreachable** — both the meeting-api callback and redis were unreachable at boot; the bot refused to join (`failed`, `failure_stage: requested`, `infra_fault: control_plane_unreachable`). Distinguishes a **broken node** from a **broken join** in one `kubectl describe`. |

## Contracts

**Owns:** none — the bot is a worker that implements published meetings contracts.
**Consumes:** [`invocation.v1`](../../contracts/invocation.v1) (boot config),
[`acts.v1`](../../contracts/acts.v1) (inbound commands),
[`lifecycle.v1`](../../contracts/lifecycle.v1) (status it produces),
[`transcript.v1`](../../contracts/transcript.v1) (segments it publishes). All four are TS-mirrored
in `src/contracts.ts` and validated against the sealed registry goldens (`contracts.seal.json`).
In authenticated mode it also follows [`session-profile.v1`](../../contracts/session-profile.v1): what
it restores and the write-back body it sends, through `@vexa/remote-browser`, which reads that
contract's profile ([`src/session-writeback.ts`](src/session-writeback.ts)).

## Browser-session mode (bot accounts)

A **bot account** is a saved signed-in browser profile a meeting bot can join as. A person creates
one through a **browser session**: a short-lived workload of this same image that meeting-api spawns
with the `meeting-bot` runtime profile and `VEXA_BROWSER_SESSION` (JSON) instead of `VEXA_BOT_CONFIG`.
`main()` sees the variable and runs [`src/browser-session.ts`](src/browser-session.ts) instead of a
meeting:

```jsonc
{"mode": "signin" | "verify", "session_id": "<32 hex>", "platform": "google" | "microsoft" | "zoom",
 "token": "<session token>", "timeout_ms": 600000,
 "vnc_password": "…", "save_url": "…",          // signin
 "restore_url": "…", "result_url": "…"}         // verify
```

Platforms map to the session profile's: google→`google`, microsoft→`teams`, zoom→`zoom`. A missing
or malformed field is refused at boot (exit 1), naming the field, never its value.

**signin.** The entrypoint has Xvfb up. The VNC password is stored with `x11vnc -storepasswd <pw>
<file>` in a private temp dir; `x11vnc -display $DISPLAY -localhost -rfbport 5900 -forever -shared
-rfbauth <file>` serves the display, and `websockify --web /usr/share/novnc 6080 localhost:5900`
bridges it to the noVNC client meeting-api relays to the person's page. The browser opens at the
platform's sign-in page (`@vexa/remote-browser` `provisionLogin`) and waits for the person. On a
confirmed sign-in the profile's SESSION_PROFILE files (`readSessionProfile`) are PUT to `save_url`
as a `session-profile.v1` `WritebackBody` with the Bearer token, and the workload exits 0; not
confirmed within `timeout_ms`, it exits 1 and sends nothing. x11vnc and websockify are stopped, and
the password file and the profile dir removed, before it exits.

**verify.** The profile is restored from `restore_url` (Bearer token; the same profile-paths-only,
fail-loud rule as a meeting bot's restore), the browser launches on it, `validateLoggedIn` decides,
and `{logged_in, detail}` is POSTed to `result_url`; exit 0. A check that cannot run (restore or
launch failed) exits 1 and posts nothing — it is not evidence that the account is signed out.

**A meeting bot that joins as a bot account** gets `sessionRestoreUrl` in its invocation
([`src/session-restore.ts`](src/session-restore.ts)): it GETs the profile from meeting-api with its
MeetingToken before launch, writes only profile paths, and writes the rotated session back on clean
teardown exactly as a store-restored bot does. Any restore failure is the same typed
`SessionSyncError('session-restore')`, so the bot never joins signed out.

### Why it complies

- **The profile is a secret.** It is the person's signed-in session. It travels only to meeting-api,
  over the bots network, with a bearer token; it is never logged (log lines are `[browser-session] …`
  and carry the mode, the platform, the session id and file counts), and the token and the VNC
  password are never logged either.
- **One listener.** x11vnc binds 127.0.0.1 only (`-localhost`), behind a per-session password
  (`-rfbauth`, never `-nopw`); websockify on 6080 is the only port the workload exposes, and the
  sign-in browser is launched without the CDP debug port (`provisionLogin` `args`, which drops
  `CDP_DEBUG_ARGS`). meeting-api relays the view only to the person who opened the session.
- **Nothing outside the profile.** What is saved and what is restored are SESSION_PROFILE paths only,
  checked by the same contract both runtimes read; a body naming one path outside it, or a traversal
  name, is refused whole.
- **Short-lived.** The workload ends when the sign-in is confirmed, times out, or fails, and removes
  its profile dir and password file before it exits.

The VNC password file stays on disk (0600 in a 0700 temp dir) until the session ends rather than
being removed right after x11vnc starts: x11vnc's `-rfbauth` reads the file at each authentication,
so removing it early would refuse every viewer.

## Isolated evaluation

Unit/integration: `pnpm test` (chained `tsx` runs — no build step). Browser-session mode and the HTTP
restore are offline-tested with fakes and a loopback meeting-api (`src/browser-session.test.ts`,
`src/session-restore.test.ts`). Levels:
**L1** config ajv goldens · **L2** orchestrator `lifecycle.v1` state machine (fake ports) — incl. the
#530 reachability gate (both-channels-down ⇒ no join, exit 3, `control_plane_unreachable`; either-channel
up ⇒ proceed) · **L3** transport adapters (lifecycle-http `emitReachable` reachability verdict ·
transcript-redis · acts-redis), pipeline lane, recording
assembler, replay tape. **L4** (browser/capture/speak/upload legs) runs via the standalone harness in
[`eval/`](./eval): `make -C eval run MEETING=<id>` drives a live Meet with synthetic speakers and an
autonomous PASS/FAIL verdict (`make -C eval verify` for the offline oracle self-test).

## Status

- ✅ delivered — `invocation.v1` boot config (parse + ajv-validate, fail-fast)
- ✅ delivered — orchestrator `lifecycle.v1` state machine (joining → admission → active → completed/failed)
- ✅ delivered — `transcript.v1` egress (redis stream + mutable pub/sub)
- ✅ delivered — `lifecycle.v1` HTTP callback (retry/backoff, never crashes the bot)
- ✅ delivered — `acts.v1` ingress (redis subscriber; unknown acts dropped, never thrown)
- ✅ delivered — recording assembler core (webm/wav/seq, L2/L3)
- 🟡 partial — browser join + capture + recording-upload + speak (wired; L4-gated, proven on VM via `eval/`, not unit tests)
