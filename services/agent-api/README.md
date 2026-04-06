# Agent API

## Why

Running an LLM agent inside a container is the easy part. The hard parts are everything around it: routing user messages to the right container, resuming sessions after container restarts, and syncing workspace files so work survives reboots. Every AI product that wants stateful agents in containers solves these problems from scratch. Agent API packages the application layer — chat routing, sessions, workspace persistence — so you wire it to [Runtime API](../runtime-api/) for container ops and get a complete agent backend.

## Data Flow

```
User / Frontend
    │
    ▼
POST /api/chat (with X-API-Key or X-User-ID)
    │
    ▼
Auth middleware validates API key (hmac.compare_digest)
    │
    ▼
container_manager: is agent container running?
    │
    no  → POST runtime-api:8090/containers (spawn container)
    yes → reuse existing
    │
    ▼
docker exec inside agent container: run agent CLI with user message
    │
    ▼
stream_parser: parse agent CLI stdout as SSE events
    │
    ▼
SSE stream back to caller: text_delta → done → stream_end
    │
    ▼
Session state saved to Redis (7-day TTL)
Workspace files synced to S3/local on container idle/stop
```

## What

AI agent runtime framework. Route user messages to LLM agents running inside ephemeral containers, with session management and workspace persistence.

## Features

- **Chat streaming** — SSE-based message routing to agents via container exec
- **Session management** — persistent sessions with Redis-backed state (7-day TTL)
- **Workspace sync** — S3-compatible workspace persistence across container restarts
- **Container lifecycle** — delegates to [Runtime API](../runtime-api/) for container orchestration
- **One agent per user** — deterministic container naming with automatic reuse

## How

### Quickstart

### Docker Compose (recommended)

```bash
docker compose up -d
```

Requires Runtime API and Redis running alongside.

### From source

```bash
pip install -e .
uvicorn agent_api.main:app --host 0.0.0.0 --port 8100
```

### Send a chat message

```bash
# Stream a response from the agent (SSE)
curl -N -X POST http://localhost:8100/api/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "user_id": "user-1",
    "message": "What files are in the workspace?"
  }'
```

### List sessions

```bash
curl http://localhost:8100/api/sessions?user_id=user-1 \
  -H "X-API-Key: your-api-key"
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send message, receive SSE stream of agent responses |
| `DELETE` | `/api/chat` | Interrupt an in-progress chat turn |
| `POST` | `/api/chat/reset` | Reset the chat session (keeps workspace files) |
| `GET` | `/api/sessions` | List all sessions for a user |
| `POST` | `/api/sessions` | Create a new named session |
| `PUT` | `/api/sessions/{id}` | Rename a session |
| `DELETE` | `/api/sessions/{id}` | Delete a session |
| `GET` | `/api/workspace/files` | List files in a user's workspace |
| `GET` | `/api/workspace/file` | Get file content from workspace |
| `POST` | `/api/workspace/file` | Write a file to the workspace |
| `POST` | `/internal/workspace/save` | Sync workspace from container to S3 |
| `GET` | `/internal/workspace/status` | Check workspace and container status |
| `GET` | `/health` | Health check |

## Architecture

```
  User / Frontend
       │
       ▼
┌──────────────┐
│ Agent API │
│              │
│ • chat SSE   │     ┌──────────────┐
│ • sessions   │────▶│  Runtime API  │──▶ Docker / K8s / Process
│ • workspaces │     └──────────────┘
└──────┬───────┘
       │
  ┌────┴────┐
  ▼         ▼
Redis    S3/MinIO
(state)  (workspaces)
```

### How chat works

1. User sends message via `POST /api/chat`
2. Agent API ensures agent container is running (via Runtime API)
3. Executes LLM CLI inside container via `docker exec`
4. Streams response back as SSE events
5. Session state saved to Redis for continuity

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_RUNTIME_PORT` | `8100` | Server port |
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL |
| `RUNTIME_API_URL` | `http://runtime-api:8090` | Runtime API for container lifecycle |
| `API_KEY` | — | API key for authentication (empty = open access) |
| `AGENT_IMAGE` | `agent:latest` | Docker image for agent containers |
| `AGENT_CLI` | `claude` | Agent CLI command inside containers |
| `AGENT_ALLOWED_TOOLS` | `Read,Write,Edit,Bash,Glob,Grep` | Tools the agent CLI can use |
| `DEFAULT_MODEL` | — | Default LLM model for the agent |
| `DOCKER_NETWORK` | — | Docker network to attach containers to |
| `CONTAINER_PREFIX` | `agent-` | Prefix for container names |
| `IDLE_TIMEOUT` | `300` | Seconds before idle containers are stopped |
| `STORAGE_BACKEND` | `local` | Storage backend: `local` or `s3` |
| `WORKSPACE_PATH` | `/workspace` | Workspace path inside containers |
| `S3_ENDPOINT` | — | S3-compatible endpoint for workspace persistence |
| `S3_ACCESS_KEY` | — | S3 access key |
| `S3_SECRET_KEY` | — | S3 secret key |
| `S3_BUCKET` | `workspaces` | S3 bucket for workspaces |
| `AGENT_WORKSPACE_PATH` | `/workspace` | Agent session storage path inside container |
| `AGENT_STREAM_FORMAT` | `stream-json` | Agent CLI output format |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `LOG_LEVEL` | `INFO` | Log level |

## Use Cases

- **AI coding assistants** — isolated dev containers with workspace persistence
- **Customer support agents** — stateful conversation agents with tool access
- **Data analysis agents** — sandboxed environments for running analysis code
- **Workflow automation** — agents that execute multi-step tasks

## Relationship to Runtime API

Agent API handles the **application layer** — chat routing, sessions, workspaces. It delegates all **container operations** (create, stop, idle management) to [Runtime API](../runtime-api/).

```
Agent API = what the agent does
Runtime API   = where the agent runs
```

## Production Readiness

**Confidence: 90/100**

| Area | Score | Evidence | Gap |
|------|-------|----------|-----|
| Core chat streaming | 9/10 | SSE routing, session resumption, retry logic, tested | No timeout on agent CLI exec |
| Session management | 9/10 | Redis-backed with 7-day TTL, CRUD endpoints, configurable paths | — |
| Workspace sync | 6/10 | S3 + local backends, path traversal protection | AWS CLI dependency undocumented; large files loaded into memory |
| Authentication | 9/10 | `hmac.compare_digest()` timing-safe comparison, open-access dev mode | CORS defaults to `*`; empty API_KEY silently disables auth |
| Container lifecycle | 6/10 | Runtime API delegation, in-memory cache, interrupt support | No cache invalidation on container death; race condition between cache check and exec |
| Tests | 9/10 | 91 unit tests covering auth, chat, endpoints, containers, streaming, workspace | No S3 integration tests |
| Docker | 9/10 | HEALTHCHECK, non-root user, standalone docker-compose, .dockerignore | — |
| Documentation | 9/10 | Accurate README, architecture diagram matches code | — |
| Standalone readiness | 9/10 | docker-compose.yml with agent-api + runtime-api + redis | — |

### Known Limitations

1. **S3 sync requires AWS CLI in container image** — undocumented dependency. If the agent image lacks `aws`, workspace persistence silently fails.
2. **No request tracing** — no correlation IDs in logs. Debugging cross-service issues requires timestamp matching.
3. **Container cache race condition** — between checking the in-memory cache and executing a command, the container can die. Chat retry logic mitigates this.

### Validation Plan (to reach 95+)

- [ ] Add S3 sync tests (use moto or MinIO testcontainer)
- [ ] Add request ID middleware for log correlation
- [ ] Add timeout on agent CLI exec
- [ ] Validate Runtime API reachability on startup with clear error message

## Code Ownership

```
agent_api/main.py              → FastAPI app, startup/shutdown, lifespan
agent_api/chat.py              → POST/DELETE /api/chat, SSE streaming, session routing
agent_api/config.py            → all settings from environment variables
agent_api/auth.py              → API key middleware (hmac.compare_digest)
agent_api/container_manager.py → Runtime API client, container cache, exec relay
agent_api/stream_parser.py     → SSE event parsing from agent CLI stdout
agent_api/workspace.py         → S3/local workspace sync, file CRUD
tests/                         → 91 unit tests (auth, chat, endpoints, containers, streaming, workspace)
```

## Constraints

- One agent container per user — deterministic naming (`{prefix}{user_id}`), automatic reuse
- Delegates ALL container operations (create, stop, exec) to Runtime API — never calls Docker directly
- Redis for session state with 7-day TTL — agent-api does not use a SQL database
- S3-compatible storage for workspace persistence — requires AWS CLI in agent image
- SSE streaming only for chat — no polling, no webhooks for response delivery
- `X-API-Key` or `X-User-ID` header required on all endpoints (empty `API_KEY` disables auth)
- Workspace file paths validated against traversal attacks (no `../`)
- Chat interrupt via `DELETE /api/chat` kills the running exec process
- `/internal/workspace/*` endpoints are internal — not exposed through api-gateway
- README.md MUST be updated when behavior changes

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns 200 | 15 | ceiling | untested | — | — | — |
| 2 | `POST /api/chat` streams SSE response with text_delta and done events | 25 | ceiling | untested | — | — | — |
| 3 | `GET /api/sessions` returns session list for user | 15 | — | untested | — | — | — |
| 4 | Redis reachable at `REDIS_URL` (session state store) | 15 | ceiling | untested | — | — | — |
| 5 | Runtime API reachable at `RUNTIME_API_URL` (container lifecycle) | 15 | ceiling | untested | — | — | — |
| 6 | `GET /api/workspace/files` lists files in user workspace | 15 | — | untested | — | — | — |

Confidence: 0 (untested)

## Known Issues

- S3 sync requires AWS CLI in agent container image — undocumented dependency, silently fails without it
- No request tracing — no correlation IDs in logs, debugging cross-service issues requires timestamp matching
- Container cache race condition — between cache check and exec, container can die (chat retry mitigates)
- CORS defaults to `*` — acceptable for dev, needs restriction in production
- Empty `API_KEY` env var silently disables authentication
- No timeout on agent CLI exec — a hung agent blocks the SSE stream indefinitely
- Large workspace files loaded into memory for S3 upload — no streaming upload

## License

Apache-2.0
