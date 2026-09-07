# deploy/compose — the v0.12 control-plane stack (P4)

`docker-compose.yml` brings up the v0.12 control plane: the infra (`postgres:17-alpine`,
`valkey/valkey:8-alpine`, `minio` + `minio-init`) and the long-running services below, each building its own
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

**The instance gate and the no-agents profile (F-D15).** Flows will not act on the world until
an admin has committed the company layer (`global_setup`) — but the only writer of that layer
is agent-api's onboarding wizard. Leave `VEXA_FLOWS_AGENT_API_URL` unset (no-agents profile)
and the gate opens BY CONSTRUCTION, since there is no wizard that could ever satisfy it; set it
(the full profile) and the gate stays fail-closed until the wizard runs, or an operator commits
it by hand over `PUT /admin/instance/global-setup` (admin-key gated, same row as the wizard's
own write).

Every service answers `GET /health` and carries a compose healthcheck; `depends_on` waits on
`condition: service_healthy` so the bring-up is ordered. The `runtime` mounts
`/var/run/docker.sock` and spawns the bot (`BROWSER_IMAGE=vexaai/vexa-bot:v012`, published — a
reference, never built here; never point it at the published `vexaai/vexa-bot:dev`, which is the
old 0.10 line and incompatible with this stack's `lifecycle.v1`) on demand and the per-dispatch
agent worker (`vexaai/v012-agent-worker:v012`, a `build-only` compose profile); neither is a
long-running compose service.

## Usage

```bash
cp .env.example .env            # edit secrets/ports/DOCKER_GID
docker compose -f deploy/compose/docker-compose.yml build
docker compose -f deploy/compose/docker-compose.yml up -d
# poll until healthy, then:
curl -sf http://localhost:18056/health   # gateway
docker compose -f deploy/compose/docker-compose.yml down -v
```

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
