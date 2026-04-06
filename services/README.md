# Services Architecture

## Why

Vexa is a dozen services, not a monolith. Each service owns one concern (transcription, bot lifecycle, user management, agent chat) and communicates via REST and Redis. This map exists because no single service tells you how the whole system works — you need the wiring diagram to understand data flow, port assignments, and which service calls which.

## What

### System Diagram

```
                      ┌─────────────┐
                      │   Dashboard  │
                      │   (Next.js)  │
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │ API Gateway  │
                      └──────┬───────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
   ┌──────▼─────┐    ┌──────▼──────┐    ┌──────▼─────┐
   │ Admin API  │    │ Meeting API │    │  Agent API  │
   │(users,     │    │(join, stop, │    │(chat, tts,  │
   │ tokens)    │    │ status, wh) │    │ workspace)  │
   └────────────┘    └──────┬──────┘    └──────┬──────┘
                            │                  │
                   ═════════╪══════════════════╪═══════
                    domain  │                  │
                    ────────┼──────────────────┘
                    infra   │
                            │
                   ┌────────▼────────┐
                   │  Runtime API    │  ← services/runtime-api/
                   │                 │
                   │ • CRUD API      │
                   │ • YAML profiles │
                   │ • idle mgmt    │
                   │ • callbacks     │
                   │ • concurrency   │
                   └────────┬────────┘
                            │
                   ┌────────┼────────┐
                   │        │        │
             ┌─────▼──┐ ┌──▼───┐ ┌──▼──────┐
             │ Docker │ │ K8s  │ │ Process │
             │ socket │ │ pods │ │ child   │
             └───┬────┘ └──┬───┘ └────┬────┘
                 │         │          │
            ┌────▼───┐ ┌───▼──┐ ┌────▼─────┐
            │vexa-bot│ │agent │ │ browser  │
            │(meetng)│ │(CLI) │ │(Chromium)│
            └────┬───┘ └──────┘ └──────────┘
                 │
         ┌───────┴────────┐
         │  Redis streams  │
         ▼                 ▼
┌──────────────┐  ┌─────────────────┐
│  Meeting API │  │ Transcription   │
│  (collector  │  │   Service       │
│   built-in)  │  │ (Whisper API)   │
└──────────────┘  └─────────────────┘
```

## Services

### API Layer

| Service | Port | Description |
|---------|------|-------------|
| [api-gateway](api-gateway/) | 8000 | Entry point. Auth middleware, routing, CORS |
| [admin-api](admin-api/) | 8001 | User management, API tokens, meeting CRUD |
| [agent-api](agent-api/) | 8100 | Chat sessions, TTS, scheduling (in-process worker), workspaces. |
| [runtime-api](runtime-api/) | 8090 | Container lifecycle API — Docker, K8s, process backends. |

### Domain Services

| Service | Description |
|---------|-------------|
| [meeting-api](meeting-api/) | Meeting domain — bot lifecycle, recordings, callbacks, webhooks |
| [vexa-bot](vexa-bot/) | Browser-based meeting bot (Zoom, Google Meet, MS Teams) |
| [vexa-agent](vexa-agent/) | Claude Code agent container |

### Transcription Pipeline

| Service | Port | Description |
|---------|------|-------------|
| [transcription-service](transcription-service/) | 8083 | Whisper API — speech-to-text |
| [transcript-rendering](../packages/transcript-rendering/) | — | TypeScript library for dedup, grouping, timestamps.  |

> **Note:** The transcription collector is now built into [meeting-api](meeting-api/). It consumes Redis streams and writes segments to PostgreSQL as part of the meeting domain service.

### Supporting Services

| Service | Port | Description |
|---------|------|-------------|
| [tts-service](tts-service/) | 8084 | Text-to-speech for voice agent participation |
| [mcp](mcp/) | 8010 | Model Context Protocol server for AI tool integration |
| [dashboard](dashboard/) | 3000 | Next.js web UI — meetings, admin, agent chat |

## Data Flow

### Meeting Transcription
1. **Meeting API** receives join request → **Runtime API** spawns **vexa-bot** container
2. **vexa-bot** joins meeting via browser, captures audio per speaker
3. Audio sent via HTTP to **Transcription Service** (Whisper) → text returned
4. Segments published to **Redis streams**
5. **Transcription Collector** consumes streams → writes to PostgreSQL
6. **Dashboard** reads transcripts from DB via **API Gateway**

### Agent Chat
1. **Agent API** receives chat request → **Runtime API** spawns **vexa-agent** container
2. **vexa-agent** runs Claude Code with workspace context
3. Responses streamed back via SSE through **Agent API** → **Dashboard**

### Scheduler

The scheduler is not a standalone service — it runs as an in-process worker inside **Agent API**. It uses Redis sorted sets to queue future HTTP calls (e.g., "join this meeting at 2pm").

**Flow:** Calendar Service syncs events → Agent API scheduler queues timed job → job fires → API Gateway → Meeting API spawns bot.

**Code:** `services/runtime-api/runtime_api/scheduler.py` + `scheduler_api.py` (moved from shared-models)

## Infrastructure Dependencies

- **PostgreSQL** — persistent storage (meetings, transcripts, users, tokens)
- **Redis** — streams (transcription segments, speaker events), pub/sub (bot commands), sorted sets (scheduler)
- **S3/MinIO** — recording storage (audio, video)

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | All API-layer services (api-gateway, admin-api, agent-api, runtime-api) health endpoints return 200 | 25 | ceiling | untested | — | — | — |
| 2 | Meeting transcription data flow works end-to-end: meeting-api -> vexa-bot -> transcription-service -> Redis -> dashboard | 25 | ceiling | untested | — | — | — |
| 3 | Agent chat data flow works: agent-api -> runtime-api -> agent container -> SSE response | 20 | ceiling | untested | — | — | — |
| 4 | PostgreSQL and Redis reachable by all services that depend on them | 15 | ceiling | untested | — | — | — |
| 5 | Inter-service routing correct (api-gateway proxies to correct backend ports) | 15 | ceiling | untested | — | — | — |

Confidence: 0 (untested)
