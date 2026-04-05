# Refactoring Execution Plan

## Goal

Restructure the monorepo so each service is a proper, self-contained Python package that can be developed together but published independently. Split bot-manager into meeting-api + runtime-api. All API contracts stay frozen.

## Target Directory Structure

```
vexa/
├── pyproject.toml                  ← workspace root (uv/hatch workspace)
├── packages/
│   ├── runtime-api/                ← generic CaaS (publishable)
│   │   ├── pyproject.toml
│   │   ├── README.md               ← "Container lifecycle API" (no meeting mentions)
│   │   ├── Dockerfile
│   │   ├── runtime_api/
│   │   │   ├── __init__.py
│   │   │   ├── main.py             ← FastAPI app
│   │   │   ├── api.py              ← /containers CRUD endpoints
│   │   │   ├── backends/
│   │   │   │   ├── __init__.py     ← Backend ABC
│   │   │   │   ├── docker.py       ← from runtime-api docker_ops + bot-manager docker.py
│   │   │   │   ├── kubernetes.py   ← from bot-manager orchestrators/kubernetes.py
│   │   │   │   └── process.py      ← from bot-manager orchestrators/process.py
│   │   │   ├── profiles.py         ← YAML-based profile loader
│   │   │   ├── lifecycle.py        ← idle management, callbacks, health
│   │   │   └── state.py            ← Redis state + reconciliation
│   │   ├── profiles.example.yaml
│   │   └── tests/
│   │
│   ├── agent-api/              ← AI agent framework (publishable)
│   │   ├── pyproject.toml
│   │   ├── README.md               ← "AI agent runtime" (no meeting mentions)
│   │   ├── Dockerfile
│   │   ├── agent_api/
│   │   │   ├── __init__.py
│   │   │   ├── main.py             ← FastAPI app
│   │   │   ├── chat.py             ← SSE streaming via container exec
│   │   │   ├── workspace.py        ← workspace sync (S3/local)
│   │   │   ├── scheduler.py        ← Redis sorted set scheduler
│   │   │   └── container_manager.py ← calls runtime-api for lifecycle
│   │   └── tests/
│   │
│   └── shared-models/              ← moved from libs/ (publishable)
│       ├── pyproject.toml
│       ├── shared_models/
│       │   ├── models.py
│       │   ├── schemas.py
│       │   └── ...
│       └── alembic/
│
├── services/                       ← Vexa-specific services (not independently publishable)
│   ├── meeting-api/                ← NEW (from bot-manager meeting domain)
│   │   ├── pyproject.toml
│   │   ├── README.md
│   │   ├── Dockerfile
│   │   ├── meeting_api/
│   │   │   ├── main.py             ← /bots/*, /recordings/*, callbacks, webhooks
│   │   │   ├── voice_agent.py      ← /speak, /chat, /screen
│   │   │   ├── recordings.py       ← recording management
│   │   │   ├── webhooks.py         ← HMAC signing, delivery, retry
│   │   │   └── post_meeting.py     ← aggregation, hooks
│   │   └── config/
│   │       └── profiles.yaml       ← Vexa-specific profiles (meeting, agent, browser)
│   │
│   ├── api-gateway/                ← unchanged
│   ├── admin-api/                  ← unchanged
│   ├── transcription-collector/    ← unchanged
│   ├── transcription-service/      ← unchanged
│   ├── tts-service/                ← unchanged
│   ├── mcp/                        ← unchanged
│   ├── calendar-service/           ← unchanged
│   ├── telegram-bot/               ← unchanged
│   ├── dashboard/                  ← unchanged (Node.js)
│   ├── vexa-bot/                   ← unchanged (Node.js)
│   ├── vexa-agent/                 ← moved from containers/agent/
│   └── transcript-rendering/       ← unchanged (Node.js)
│
├── deploy/                         ← unchanged
├── docs/                           ← unchanged
├── features/                       ← unchanged (dev workspace)
└── tests/                          ← unchanged
```

### Key decisions

**packages/ vs services/**
- `packages/` = independently publishable, generic, no Vexa domain knowledge
- `services/` = Vexa-specific, depends on packages, not published independently

**What's publishable (packages/):**
- `runtime-api` → `pip install vexa-runtime` / `docker pull vexa-runtime`
- `agent-api` → `pip install vexa-agents` / `docker pull vexa-agents`
- `shared-models` → `pip install vexa-models` (DB models, schemas)

**What's Vexa-only (services/):**
- `meeting-api` — meeting domain (depends on runtime-api)
- Everything else — Vexa product services

---

## Phase 0: Package Foundation (no code changes)

Create the workspace structure without moving any code yet.

### 0.1 Create workspace root pyproject.toml

```toml
[project]
name = "vexa"
version = "0.1.0"

[tool.uv.workspace]
members = ["packages/*", "services/*"]
```

### 0.2 Add pyproject.toml to each service

Every service that has `requirements.txt` gets a `pyproject.toml` that declares its dependencies. The `requirements.txt` stays for Docker builds (simpler).

### 0.3 Move shared-models

```
libs/shared-models/ → packages/shared-models/
```

Update all imports (they already use `from shared_models import ...` so this is a path change, not a code change).

---

## Phase 1: Extract Runtime API (the generic CaaS)

### 1.1 Create services/runtime-api/

Start with current `services/runtime-api/` code. Add:
- Backend ABC from architecture plan
- Docker backend (current `docker_ops.py`)
- K8s backend (port from `bot-manager/app/orchestrators/kubernetes.py`)
- Process backend (port from `bot-manager/app/orchestrators/process.py`)
- YAML profile loader (replace hardcoded dict)
- Lifecycle callbacks (`callback_url` parameter at creation time)
- Per-user concurrency limits

### 1.2 Strip Vexa-specific code

- Remove hardcoded profiles (meeting/agent/browser) → move to YAML config
- Remove internal hostnames as defaults
- Remove shared_models auth imports → Protocol interface
- Generic README (no meeting mentions)

### 1.3 Add callback_url to container creation

```python
POST /containers
{
    "profile": "meeting",
    "config": {"meeting_url": "...", "platform": "zoom"},
    "user_id": "123",
    "callback_url": "http://meeting-api:8080/internal/callback",
    "metadata": {"meeting_id": 456}  # returned in callbacks
}
```

### 1.4 Feature toggle in bot-manager

Bot-manager gains `USE_RUNTIME_API=true/false`. When true, calls Runtime API instead of Docker directly. Both paths run during testing.

---

## Phase 2: Create Meeting API

### 2.1 Create services/meeting-api/

Extract from bot-manager:
- `POST /bots` → meeting creation + calls Runtime API
- `DELETE /bots/{platform}/{id}` → calls Runtime API to stop
- `GET /bots/{id}/status` → reads from DB
- Voice agent: `/speak`, `/chat`, `/screen`
- Recordings: `/recordings/*`
- Callbacks: `/internal/callback/*`
- Webhooks: delivery, HMAC signing, retry
- Post-meeting: aggregation, hooks

### 2.2 Meeting API calls Runtime API

```python
# meeting-api creates a bot
resp = await httpx.post(f"{RUNTIME_API_URL}/containers", json={
    "profile": "meeting",
    "config": bot_config,
    "user_id": str(user.id),
    "callback_url": f"http://meeting-api:8080/internal/callback/exited",
    "metadata": {"meeting_id": meeting.id}
})
```

### 2.3 Gateway routing change (one line)

```python
# Before (Phase 2)
BOT_MANAGER_URL = os.getenv("BOT_MANAGER_URL", "http://meeting-api:8080")
# Final (post-finalization)
MEETING_API_URL = os.getenv("MEETING_API_URL", "http://meeting-api:8080")
```

Env var renamed from BOT_MANAGER_URL to MEETING_API_URL in the finalization phase.

### 2.4 Frozen contracts

Meeting API returns exact same response shapes:
- `/bots/status` → `{"running_bots": [...]}` with `meeting_id_from_name`, `container_name`, etc.
- `/bots` POST → same `{"id": ..., "platform": ..., "status": ...}`
- Redis channels → same `bm:meeting:{id}:status` prefix

---

## Phase 3: Extract Agent API

### 3.1 Create services/agent-api/

Move from `services/agent-api/`:
- Chat streaming (SSE via docker exec)
- Container manager (calls runtime-api)
- Workspace sync
- Scheduler (from shared-models)

### 3.2 Strip Vexa-specific code

- Generic README (no meeting mentions)
- Pluggable auth (Protocol interface)
- No hardcoded service names

### 3.3 Agent API becomes thin Vexa wrapper

```
services/agent-api/  → imports from services/agent-api/
                     → adds Vexa-specific auth, config, TTS integration
```

---

## Phase 4: Delete bot-manager

### 4.1 Verify all traffic routes through meeting-api

- All `/bots/*` requests served by meeting-api
- All callbacks received by meeting-api
- All voice agent commands work through meeting-api
- All recordings served by meeting-api

### 4.2 Delete

```bash
git rm -r services/bot-manager/
```

### 4.3 Move containers/agent/ → services/vexa-agent/

### 4.4 Update deploy configs

- docker-compose: remove bot-manager, add meeting-api
- Helm charts: same
- Vexa Lite: update process list

---

## Phase 5: Polish for publishing

### 5.1 Each publishable package gets:

- [ ] Clean pyproject.toml with proper metadata (author, license, URLs)
- [ ] README with no Vexa-specific language
- [ ] Dockerfile
- [ ] CHANGELOG.md
- [ ] Tests that run standalone
- [ ] `profiles.example.yaml` (runtime-api)

### 5.2 CI pipeline for subtree publishing

Proven pattern used by Symfony (50+ packages), Laravel (28 packages). Tool: `splitsh-lite` via `danharrin/monorepo-split-github-action`.

**On merge to main:** split subtree → force-push to mirror repo (preserves all commit history + authors)
**On tag:** split + tag mirror + publish to PyPI (OIDC, no stored tokens) + build/push Docker to GHCR

Setup:
1. Create empty mirror repos: `vexa-ai/vexa-runtime`, `vexa-ai/vexa-agents`
2. Create fine-grained PAT with `contents: write` on mirror repos → `SPLIT_ACCESS_TOKEN` secret
3. Configure PyPI trusted publisher (OIDC, no tokens stored)
4. Add auto-close PR workflow to each mirror repo (redirects to monorepo)

See `docs/runtime-api-oss-strategy.md` for the full GitHub Actions YAML.

### 5.3 Contributor attribution

`splitsh-lite` creates synthetic commits preserving the original author, date, and message — just rewrites paths to repo root. All contributor commits automatically appear in mirror repos with proper attribution. agrogov's, jbschooley's, and all other contributors' work carries through.

### 5.4 Mirror repo auto-close PRs

Each mirror repo gets `.github/workflows/close-prs.yml` that auto-closes PRs with a message redirecting to the monorepo. This prevents contributor confusion.

---

## Execution Order

```
Phase 0 ─── foundation (1-2 days, no risk)
   │
Phase 1 ─── runtime-api extraction (1 week)
   │         feature toggle in bot-manager
   │
Phase 2 ─── meeting-api creation (1 week)
   │         gateway routing change
   │         run both paths, validate
   │
Phase 3 ─── agent-api extraction (3-4 days)
   │
Phase 4 ─── delete bot-manager (1 day)
   │
Phase 5 ─── publish setup (2-3 days)
```

Each phase is independently deployable. Rollback = revert gateway routing.

---

## Constraints (from architecture plan)

1. **API contracts frozen** — `/bots/*`, `/recordings/*`, `/meetings/*`, WebSocket all stay as-is
2. **Three backends** — Process (Lite), Docker (dev), K8s (prod)
3. **No new infrastructure** — Postgres + Redis + S3 only
4. **Contributor attribution preserved** — git history carries through subtree splits
5. **Redis channel names frozen** — `bm:meeting:*`, `tc:meeting:*`, `va:meeting:*`
6. **Response shapes frozen** — `/bots/status` returns exact same fields
