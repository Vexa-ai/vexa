# deploy/compose — the v0.12 control-plane stack (P4)

`docker-compose.yml` brings up the v0.12 control plane: the infra (`postgres:17-alpine`,
`valkey/valkey:8-alpine`, `storage` (versitygw) + `storage-init`) and the long-running services below, each building its own
slim image from `<service>/Dockerfile`:

| service      | build context                          | host port | entrypoint                         |
|--------------|----------------------------------------|-----------|------------------------------------|
| admin-api    | `core/identity/services/admin-api`     | 18057     | `python -m admin_api`              |
| runtime      | `core/runtime`                         | 18090     | `python -m runtime_kernel`         |
| meeting-api  | `core/meetings/services/meeting-api`   | 18080     | `python -m meeting_api`            |
| agent-api    | `core/agent/services/agent-api`        | 18100     | `uvicorn control_plane.api`        |
| gateway      | `core/gateway/services/gateway`        | 18056     | `python -m gateway`                |
| terminal     | `clients/terminal`                     | 13000     | Next.js custom server              |
| mcp          | `core/meetings/services/mcp`           | 18010     | the MCP transport                  |
| flows-api    | repo root, `core/flows/Dockerfile`     | 18200     | `python -m flows_integrations.flows_api` |
| flows-mailbox| repo root, `core/flows/Dockerfile`     | —         | `python -m flows_integrations.mailbox` (profile `mailbox`) |

### flows, and what it replaces

`flows-api` is the reaction engine's HTTP surface and one of the domains the MCP assembly asks for
a tool manifest (PRD decision 40): `mcp` fetches `/.well-known/mcp-tools.json` from it, so a stack
that runs flows serves flows' tools on the one MCP surface, and one that does not simply serves
fewer. Before this service existed the engine ran as HOST processes beside the stack and `mcp` was
pointed at the docker BRIDGE ADDRESS of that host lane — a host-specific IP written into a
deployment, for a service the deployment did not run. `FLOWS_API_URL` now defaults to
`http://flows-api:8200`; set it to point at a flows elsewhere, or set it EMPTY to run a deployment
that genuinely does not carry the domain.

An existing deployment that reaches flows through such a bridge keeps working: its
`VEXA_FLOWS_API_URL` override still wins over the new default, so the host lane and any listener
in front of it retire on the operator's own schedule, after the stack is cut over — not on the day
this merges.

`flows-mailbox` is the inbound mail lane (IMAP poll → `POST /events`), the same image under a
different command and with the same environment. It is behind the `mailbox` COMPOSE PROFILE and
therefore off by default, because mail is an optional intake: a lane started without real IMAP
credentials restart-loops and reads as a broken stack. Turn it on with `--profile mailbox` (or
`COMPOSE_PROFILES=mailbox`) once `VEXA_MAIL_ADDR` and `VEXA_MAIL_APP_PASSWORD` are set. Do not
scale it — the IMAP cursor is single-writer by design.

Two things flows will refuse, and both are deliberate: it will not start without
`VEXA_FLOWS_API_KEY`, `VEXA_FLOWS_ADMIN_KEY` and `INTERNAL_API_SECRET` (a weak default makes an
unconfigured deployment look configured), and it will refuse to compose a mailed link when
`VEXA_UI_URL` is unset — at the link, not at boot, because a deployment may legitimately have no
terminal. Every key it reads is declared in `core/flows/src/config.v1.json` and checked against
this file by `gate:config-contract`.

**First run needs no setup step.** The first person to sign in becomes the administrator; everyone
who signs in after them is served at once. There is no company-layer gate (founder ruling
2026-10-08): `_global` lives in the `agent-workspaces` volume, agent-api creates it empty at boot,
and flows act on the world whether or not anybody ever writes it. `VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH`
is optional — set it only to manage `_global` as a separate host repo.

**Who may sign in.** Every terminal door — the emailed link, Google, Microsoft — admits only an
existing user of this instance, an admin, or an address on the sign-in allow-list; anybody else is
refused before an account is created, and the emailed-link form answers "check your email" either
way without sending them anything. The allow-list is `VEXA_SIGNIN_ALLOW` on `admin-api` (exact
addresses and `@domain` entries, comma-separated, e.g. `@example.com,alice@example.org`) plus
whatever the admin adds in the terminal under Settings → Sign-in. If admin-api cannot be reached,
new sign-ins are refused; sessions that already exist are untouched. Upgrading admits every
account that already exists, so nobody is locked out by it.

⚠ **Until an admin is claimed, anybody may sign in** — that first sign-in becomes the admin.
`VEXA_ADMIN_EMAILS` names the admins and turns the claim off, so **set it in `.env` before first
boot whenever the terminal is reachable from outside.**

Every service answers `GET /health` and carries a compose healthcheck; `depends_on` waits on
`condition: service_healthy` so the bring-up is ordered. The `runtime` mounts
`/var/run/docker.sock` and spawns the bot (`BROWSER_IMAGE=vexaai/vexa-bot:v012`, published — a
reference, never built here; never point it at the published `vexaai/vexa-bot:dev`, which is the
old 0.10 line and incompatible with this stack's `lifecycle.v1`) on demand and the per-dispatch
agent worker (`vexaai/v012-agent-worker:v012`, a `build-only` compose profile); neither is a
long-running compose service.

## Usage

```bash
./deploy/compose/mint-dev-env.sh   # seeds .env, mints the secrets the services refuse to boot without; then edit ports/DOCKER_GID
docker compose -f deploy/compose/docker-compose.yml build
docker compose -f deploy/compose/docker-compose.yml up -d
# poll until healthy, then:
curl -sf http://localhost:18056/health   # gateway
docker compose -f deploy/compose/docker-compose.yml down
```

`make -C deploy/compose down` removes containers and keeps data volumes. To deliberately delete
the database, recordings, Redis data and agent workspaces, use
`make -C deploy/compose destroy DESTROY=yes`. `make -C deploy/compose help` lists both commands.

Storage readiness checks the same endpoint as meeting-api (`S3_ENDPOINT`, else `MINIO_ENDPOINT`
with `MINIO_SECURE`), creates the bucket if absent, and verifies a PUT/GET/DELETE probe outside
`recordings/`. Any failure prevents meeting-api from starting. After a MinIO upgrade, old
recordings stay in the old volume and do not play back from the new storage until copied with
`make -C deploy/compose migrate-storage`. Update `.env`'s old endpoint to `storage:9000` as shown
in the [upgrade guide](../../docs/docs/upgrade-from-minio.mdx).

`.env.example` documents every variable (faithful to the 0.11 `deploy/compose` names: `DB_*`,
`REDIS_URL`, `ADMIN_TOKEN`, `INTERNAL_API_SECRET`, `MINIO_*`, `BROWSER_IMAGE`/`AGENT_IMAGE`,
`DOCKER_GID`, `*_HOST_PORT`).

## Smoke probe — "is this install actually working?"

```bash
make probe                       # from the repo root (compose is the default surface)
```

Drives the ONE full journey through the gateway front door — spawn → schedule → boot → join →
transcribe → live-view → stop — then sweeps every component's logs once. Each stage prints
Expected / Actual / Verdict; a red stage names where the journey broke and fails the command.
With the mock bot as `BROWSER_IMAGE` (`mock-bot:dev`) the journey is a deterministic green,
transcript included; with the real bot it drives a dead synthetic meeting to a truthful named
`join_failure`. See `deploy/compose/probe.sh` (a wrapper over `scripts/probe/journey.sh`).
