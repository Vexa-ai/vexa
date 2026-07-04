# deploy/compose/tests — the gate:compose stack-readiness proof (P5)

`stack_test.py` brings up the **real** v0.12 compose stack (see `../docker-compose.yml`), proves it
is ready to run the vexa bot, and tears it all down (`down -v`) in a guaranteed finally — the
"fully tested / proven ready" deliverable wired as **`gate:compose`** in `scripts/gates.mjs`.

The `stack` session fixture (`conftest.py`) owns the whole lifecycle: `docker compose up -d --build`
under an isolated project name, a bounded wait for every service `healthy`, then `down -v` on exit.
Everything polls with bounded timeouts — never sleep-and-hope. Absent docker → the module self-skips
(green-or-skip).

## What it proves

| step | proof | always-on? |
|------|-------|------------|
| 1  | `/health` 200 on gateway·meeting-api·runtime·admin-api | yes |
| 2  | admin-api mints a scoped token; gateway accepts it (200), rejects missing/invalid (401) + out-of-scope (403); a proxied call reaches meeting-api | yes |
| 4  | XADD golden segments → the collector consumer stores them (live segment hash) + publishes `tc:meeting:{id}:mutable` → a `/ws` client (through the gateway) receives the live frame | yes |
| 5  | upload a chunk via the bot's `/internal/recordings/upload` → the object lands in minio; finalize → a master is assembled in minio | yes |
| 6c | `continue_meeting` reuses the meeting row + accumulates a session; the prior transcript survives | yes |
| 6d | a user at `max_concurrent_bots` gets `429` on the N+1; a freed slot admits the next | yes |
| 6b | the join-retry re-spawn wiring is present in the live image; the **backoff proof leans on the offline P3 `test_join_retry.py`** (forcing a real transient join-failure on a live bot is slow/flaky) | yes (wiring) |
| 3  | `POST /bots` → meeting-api spawns via runtime → a real `vexa-mtg-…` container appears in `docker ps` AND the meeting advances to `joining`; then the bot is stopped + cleaned | **`COMPOSE_BOT=1`** |
| 6a | start-then-stop a real bot → terminal; the leave-command channel wiring is asserted | **`COMPOSE_BOT=1`** |

## Run

```bash
make -C deploy/compose stack-test        # always-on subset (what gate:compose runs)
make -C deploy/compose stack-test-bot    # + the real bot-spawn proof (COMPOSE_BOT=1, slow ~7GB image)
node scripts/gates.mjs compose           # the wired gate (docker absent → green skip)
```

### Always-on vs `COMPOSE_BOT`-gated — why

Steps 3 and 6a drive a **real `vexaai/vexa-bot:v012` container** (Playwright browser, ~7GB image)
through its lifecycle. That is slow and inherently flaky for a routine gate (browser boot, a dummy
meeting URL, callback timing). The fast steps (1·2·4·5·6c·6d·6b) exercise the entire control plane —
auth, the transcript bus, recordings→object-store, and the lifecycle/scheduling wiring — with no real
bot, deterministically, so they are the always-on `gate:compose`. The bot-spawn proof is **runnable**
(`COMPOSE_BOT=1`) and is the thing that finally says "a real bot spawns and reaches `joining`".

### FLAGGED — the real bot-spawn (step 3) cannot pass on the current stack yet

Running `COMPOSE_BOT=1` against this stack root-caused **three real carve gaps** that block a live
bot spawn (all surfaced by the live stack, none by the offline gates):

1. **meeting-api had no `ADMIN_TOKEN`** — the bot-spawn flow mints a MeetingToken, and the shipped
   `invocation.mint_meeting_token` (called with no `token_secret` from `bot_spawn`) signs it with
   `os.environ["ADMIN_TOKEN"]`; the P4 compose env never set it → every `POST /bots` 500'd. **Fixed
   here** by pinning `ADMIN_TOKEN=${INTERNAL_API_SECRET}` on the meeting-api service in
   `../docker-compose.yml` (so mint and the recordings verifier — which uses `INTERNAL_API_SECRET` —
   agree). Deeper fix (out of this gate's scope): `app.create_app` should forward `token_secret` into
   the `bot_spawn` router too, not only recordings.
2. **`updated_at` tz mismatch** — `bot_spawn/adapters.SqlAlchemyMeetingRepo.set_bot_container` /
   `reopen_meeting` write `datetime.now(timezone.utc)` (offset-aware) into the `meetings.updated_at`
   `TIMESTAMP WITHOUT TIME ZONE` column → asyncpg `DataError: can't subtract offset-naive and
   offset-aware datetimes`. A shipped meeting-api bug (out of this gate's edit scope).
3. **the runtime image has no `docker` CLI** — `runtime/Dockerfile` does `apt-get install docker.io`,
   but on Debian-slim that package ships only the **daemon** (`dockerd`/`docker-proxy`/`docker-init`),
   not the `docker` *client* the `DockerBackend` shells out to. So every spawn raises FileNotFoundError
   → the runtime returns the workload as `stopped/start_failed` and **no `vexa-…` container is ever
   created**. A shipped runtime-image bug (out of this gate's edit scope; needs the real Docker CLI in
   the image).

Step 3 stays present + runnable and now **fails with a self-explaining diagnostic** (it reports the
runtime workload's `stopReason`), so the moment gaps 2+3 are fixed it goes green. The always-on subset
is unaffected by these (it never spawns a real container) and is fully green on the live stack.

### Note on `GET /transcripts` vs the `:mutable` frame (step 4)

In this carve the collector's live-path stores in-flight segments in the redis hash
`meeting:{id}:segments` and publishes the `:mutable` update; `GET /transcripts` reads the postgres
`transcriptions` table (the parent's background db-writer that flushes the hash → table is out of this
carve's scope). So step 4 proves the **store** side via the redis hash and the **publish** side via the
live `/ws` frame — the two observable halves the running stack actually produces.
