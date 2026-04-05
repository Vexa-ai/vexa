# Proposed Architecture

## Why

The current codebase mixes generic infrastructure with Vexa-specific product code in the same directories, shares a single database schema across all services, and has no clear ownership boundaries. This makes it impossible to publish runtime-api or agent-api standalone without dragging in Vexa's Postgres schema, auth logic, and meeting-specific enums.

The proposed architecture draws clean lines: each package owns its own data, auth flows through the gateway as headers, and shared state is just an integer (`user_id`) — not a model import.

## System Diagram

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                      Clients                            │
                    │   Dashboard    Telegram Bot    CLI    External Apps     │
                    └────────────────────┬────────────────────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────────────────────┐
                    │                   API Gateway                           │
                    │                                                         │
                    │  • Token validation (calls admin-api)                   │
                    │  • Injects: X-User-ID, X-User-Scopes, X-User-Limits   │
                    │  • CORS, rate limiting, WebSocket fan-out              │
                    │  • Routes to domain APIs by path prefix               │
                    │                                                         │
                    │  /bots/*        → meeting-api                          │
                    │  /agents/*      → agent-api                            │
                    │  /admin/*       → admin-api                            │
                    │  /calendar/*    → calendar-service                     │
                    └──────┬──────────────┬──────────────┬──────────────┬────┘
                           │              │              │              │
              ┌────────────▼──┐  ┌────────▼────┐  ┌─────▼─────┐  ┌───▼───────────┐
              │  meeting-api  │  │  agent-api   │  │ admin-api │  │   calendar    │
              │  (package)    │  │  (package)   │  │ (service) │  │   -service    │
              │               │  │              │  │           │  │               │
              │ • Join/stop   │  │ • Chat (SSE) │  │ • Users   │  │ • Google Cal  │
              │ • Transcripts │  │ • Workspace  │  │ • Tokens  │  │ • Auto-join   │
              │ • Recordings  │  │ • Sessions   │  │ • Scopes  │  │ • Scheduling  │
              │ • Voice agent │  │              │  │ • Limits  │  │   (via runtime│
              │ • Webhooks    │  │              │  │           │  │    scheduler) │
              │ • Callbacks   │  │              │  │           │  │               │
              │               │  │              │  │           │  │               │
              │ owns:         │  │ owns:        │  │ owns:     │  │               │
              │  meetings     │  │  sessions    │  │  users    │  │               │
              │  transcripts  │  │  workspaces  │  │  tokens   │  │               │
              │  recordings   │  │              │  │           │  │               │
              │  media_files  │  │              │  │           │  │               │
              │  webhooks     │  │              │  │           │  │               │
              └───────┬───────┘  └───────┬──────┘  └─────┬─────┘  └───────────────┘
                      │                  │               │
                      │    user_id is    │               │
                      │    just an int   │           owns users
                      │    from headers  │           table
                      │                  │               │
              ════════╪══════════════════╪═══════════════╪══════════
               domain │                  │               │
              --------│------------------│---------------│----------
               infra  │                  │               │
                      │                  │               │
                      └────────┬─────────┘               │
                               │                         │
                      ┌────────▼────────┐                │
                      │   runtime-api   │                │
                      │   (package)     │                │
                      │                 │                │
                      │ • Container     │                │
                      │   CRUD          │                │
                      │ • Profiles      │                │
                      │ • Idle mgmt    │                │
                      │ • Callbacks     │                │
                      │ • Scheduler     │                │
                      │                 │                │
                      │ backends:       │                │
                      │  Docker         │                │
                      │  Kubernetes     │                │
                      │  Process        │                │
                      └────────┬────────┘                │
                               │                         │
                      ┌────────┼────────┐                │
                      │        │        │                │
                ┌─────▼──┐ ┌──▼───┐ ┌──▼──────┐         │
                │vexa-bot│ │vexa- │ │ browser │         │
                │(meeting│ │agent │ │ session │         │
                │ bot)   │ │(CLI) │ │         │         │
                └───┬────┘ └──────┘ └─────────┘         │
                    │                                    │
            ┌───────┴────────┐                           │
            │  Redis streams  │                          │
            ▼                 ▼                           │
   ┌──────────────┐  ┌──────────────┐                    │
   │transcription │  │transcription │                    │
   │ -collector   │  │  -service    │                    │
   │ (internal to │  │  (package)   │                    │
   │  meeting-api)│  │  Whisper API │                    │
   └──────┬───────┘  └──────────────┘                    │
          │                                              │
          ▼                                              │
   ┌──────────────────────────────────────────────────────┐
   │                    PostgreSQL                         │
   │                                                       │
   │  ┌─────────┐  ┌───────────┐  ┌────────────────────┐  │
   │  │ admin   │  │ meeting   │  │ agent              │  │
   │  │ schema  │  │ schema    │  │ schema             │  │
   │  │         │  │           │  │                    │  │
   │  │ users   │  │ meetings  │  │ agent_sessions     │  │
   │  │ tokens  │  │ sessions  │  │ workspaces         │  │
   │  │         │  │ transcrip │  │                    │  │
   │  │         │  │ recordings│  │                    │  │
   │  │         │  │ media     │  │                    │  │
   │  │         │  │ webhooks  │  │                    │  │
   │  └─────────┘  └───────────┘  └────────────────────┘  │
   │                                                       │
   │  FK: meetings.user_id → users.id (DB level only)     │
   │  FK: agent_sessions.user_id → users.id (DB level)    │
   │  No cross-schema model imports in code                │
   └───────────────────────────────────────────────────────┘

   ┌───────────────┐  ┌───────────────┐
   │     Redis      │  │   S3 / MinIO  │
   │                │  │               │
   │ • streams      │  │ • recordings  │
   │ • pub/sub      │  │ • workspaces  │
   │ • scheduler    │  │ • media files │
   │   sorted sets  │  │               │
   └───────────────┘  └───────────────┘
```

## Auth Flow

```
Client                    Gateway                  Admin-API            Meeting-API
  │                         │                         │                     │
  │ X-API-Key: vxa_bot_xxx  │                         │                     │
  ├────────────────────────►│                         │                     │
  │                         │ POST /internal/validate │                     │
  │                         │  { token: "vxa_bot_xxx"}│                     │
  │                         ├────────────────────────►│                     │
  │                         │                         │                     │
  │                         │ { user_id: 5,           │                     │
  │                         │   scopes: ["bot"],      │                     │
  │                         │   max_concurrent: 3 }   │                     │
  │                         │◄────────────────────────┤                     │
  │                         │                         │                     │
  │                         │ X-User-ID: 5            │                     │
  │                         │ X-User-Scopes: bot      │                     │
  │                         │ X-User-Limits: 3        │                     │
  │                         ├─────────────────────────┼────────────────────►│
  │                         │                         │                     │
  │                         │                         │   meeting-api reads │
  │                         │                         │   headers, never    │
  │                         │                         │   queries users     │
  │                         │                         │   table directly    │
```

## Data Ownership

Each service owns its tables. No cross-service model imports.

| Service | Tables it owns | Tables it reads |
|---------|---------------|-----------------|
| admin-api | `users`, `api_tokens` | — |
| meeting-api | `meetings`, `meeting_sessions`, `transcriptions`, `recordings`, `media_files`, `webhooks` | — |
| agent-api | `agent_sessions`, `workspaces` | — |

Cross-references are integers: `meetings.user_id = 5`. The FK exists at the DB level for integrity. The code never does `from admin_api.models import User`.

## Packages vs Services

**Packages** — independently publishable, generic value:

| Package | Standalone use case | Competitors |
|---------|-------------------|-------------|
| `runtime-api` | Container lifecycle API | Fly Machines, E2B |
| `agent-api` | AI agent sessions + chat | E2B, Daytona |
| `meeting-api` | Meeting bot management | Recall.ai, Attendee |
| `transcription-service` | Speech-to-text API | Deepgram, AssemblyAI |
| `tts-service` | Text-to-speech API | ElevenLabs, PlayHT |

**Services** — Vexa internal, not published:

| Service | Why not a package |
|---------|------------------|
| `admin-api` | Vexa auth/user management (could become a package later) |
| `api-gateway` | Vexa-specific routing, wires Vexa services together |
| `calendar-service` | Google Calendar integration (could be generic later) |
| `telegram-bot` | Vexa Telegram client |
| `dashboard` | Vexa Next.js UI |
| `transcription-collector` | Internal to meeting-api (Redis stream → Postgres writer) |

**Internal libs** (shared across Vexa services, not published):

| Lib | What |
|-----|------|
| `shared-models` | Vexa DB schema — will be split: each package owns its models, this becomes admin-only (users/tokens) |
| `transcript-rendering` | Transcript UI processing — used by dashboard, internal to Vexa |

## Auth Model

Packages are infrastructure, not products with accounts. They don't manage users.

### Standalone (someone installs just meeting-api)

```
Deployer sets: API_KEYS=key1,key2,key3

Client → meeting-api
         │
         ├── check X-API-Key against API_KEYS env var
         ├── user_id comes from request body (opaque string)
         └── no users table, no token lifecycle, no scopes
```

The deployer manages auth however they want — API keys in env, OAuth proxy in front, custom middleware. The package just needs to know "is this request allowed" (API key check) and "who is it from" (`user_id` parameter).

This is how Temporal, MinIO, and Traefik work. Infrastructure packages don't manage users. User management is a product concern.

### Inside Vexa (full monorepo deployment)

```
Client → api-gateway → admin-api (validate vxa_bot_xxx token)
                      ← { user_id: 5, scopes: [bot], max_concurrent: 3 }
         │
         ├── inject X-User-ID: 5
         ├── inject X-User-Scopes: bot
         ├── inject X-User-Limits: 3
         │
         └── meeting-api reads headers, stores user_id=5
             never queries users table
```

Admin-api owns user lifecycle (create, delete, tokens, scopes, limits). Gateway is the auth boundary. Packages downstream are unaware of how auth works — they just read headers.

### Auth interface in each package

```python
# Each package ships a simple auth module:

async def validate_request(request: Request) -> dict:
    """Returns {user_id, scopes} or raises 401/403.

    Checks in order:
    1. X-User-ID header (trusted — set by gateway behind a reverse proxy)
    2. X-API-Key header (standalone — checked against API_KEYS env var)
    3. Neither → 401
    """
```

No Protocol class, no dependency injection, no plugin system. Just a function that checks headers. Works in both modes.

## Migration Path

This is the target. Getting there from current state:

| Step | What | Breaks anything? |
|------|------|-------------------|
| 1 | Move `tts-service` → `services/tts-service/` | No — same code, new location |
| 2 | Move `transcription-service` → `services/transcription-service/` | No |
| 3 | Move `meeting-api` → `services/meeting-api/` | No |
| 4 | Add `X-User-ID` header injection to gateway | No — additive |
| 5 | Meeting-api reads `X-User-ID` from headers instead of querying User | Requires gateway change deployed first |
| 6 | Split shared-models: meeting models → meeting-api, agent models → agent-api | Biggest change — needs migration coordination |
| 7 | Move `shared-models` → `libs/admin-models` (users + tokens only) | After step 6 |
| 8 | Fold `transcription-collector` into meeting-api | Internal refactor |
| 9 | Move `transcript-rendering` → `libs/transcript-rendering` | Dashboard import path change |
