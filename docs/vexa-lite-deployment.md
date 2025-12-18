# Vexa Lite Deployment Guide

Deploy Vexa as a single Docker container with no GPU requirements. Vexa Lite is a stateless container that connects to external database and transcription services.

## Overview

**Why Vexa Lite?**

- **Easy deployment** — Single container, no multi-service orchestration required
- **Stateless** — All data stored in your database; easy to redeploy and scale
- **No GPU required** — Transcription runs outside the container (hosted or self-hosted)
- **Flexible** — Mix and match database and transcription service locations

## Deployment Options

You can configure Vexa Lite with different combinations of database and transcription services:

| Database | Transcription | Use Case |
|----------|---------------|----------|
| Remote | Remote | Fastest setup, GPU-free, production-ready |
| Remote | Local | Maximum privacy with on-premise transcription |
| Local | Remote | Quick development setup |
| Local | Local | Full self-hosting, complete data sovereignty |

---

## Step 1: Choose Transcription Service

### Option A: Use Hosted Transcription Service (Recommended)

Get your API key from [vexa.ai](https://vexa.ai):

- `TRANSCRIBER_URL`: `https://transcription.vexa.ai/v1/audio/transcriptions`
- `TRANSCRIBER_API_KEY`: Your API key from vexa.ai

**Benefits:** GPU-free, better scalability, managed infrastructure

### Option B: Self-Host Transcription Service

Run the transcription service locally for on-premise data processing:

See [services/transcription-service/README.md](../services/transcription-service/README.md) for setup instructions.

- `TRANSCRIBER_URL`: `http://host.docker.internal:8083/v1/audio/transcriptions` (when running locally)
- `TRANSCRIBER_API_KEY`: Your transcription service API key

**Benefits:** Complete data sovereignty, all processing on-premise

---

## Step 2: Choose Database

### Option A: Use Hosted Database (Recommended for Production)

#### Using Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **Settings** → **Database**
3. Copy the connection pooler URL

Example connection string format:
```
postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres
```

Set `DATABASE_URL` to your connection string and `DB_SSL_MODE=require`.

**Benefits:** Managed backups, high availability, production-ready

### Option B: Run Local PostgreSQL

Perfect for development or when you need everything on-premise:

```bash
# Create network (if not exists)
docker network create vexa-network 2>/dev/null || true

# Start PostgreSQL
docker run -d \
  --name vexa-postgres \
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  postgres:latest
```

Connection string: `postgresql://postgres:your_password@vexa-postgres:5432/vexa`

**Note:** When using local PostgreSQL, Vexa container must be on the same Docker network (`vexa-network`).

**Benefits:** Faster start, no external dependencies, full control

---

## Step 3: Run Vexa Lite Container

### Basic Example

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.example.com/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="token" \
  vexaai/vexa-lite:latest
```

**API available at:** `http://localhost:8056`

---

## Complete Setup Examples

### Example 1: Remote Database + Remote Transcription

**Best for:** Production deployments, fastest setup

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-api-key" \
  vexaai/vexa-lite:latest
```

---

### Example 2: Local Database + Remote Transcription

**Best for:** Development, quick testing

```bash
# 1. Create network
docker network create vexa-network

# 2. Start PostgreSQL
docker run -d \
  --name vexa-postgres \
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  postgres:latest

# 3. Start Vexa Lite
docker run -d \
  --name vexa \
  --network vexa-network \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-api-key" \
  vexaai/vexa-lite:latest
```

---

### Example 3: Remote Database + Local Transcription

**Best for:** Maximum privacy with managed database

```bash
# 1. Start transcription service (see services/transcription-service/README.md)
cd services/transcription-service/
docker compose -f docker-compose.cpu.yml up -d

# 2. Start Vexa Lite
docker run -d \
  --name vexa \
  --add-host=host.docker.internal:host-gateway \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-transcription-api-key" \
  vexaai/vexa-lite:latest
```

**Note:** Use `--add-host=host.docker.internal:host-gateway` to access the transcription service running on the host.

---

### Example 4: Local Database + Local Transcription

**Best for:** Complete self-hosting, full data sovereignty

```bash
# 1. Create network
docker network create vexa-network

# 2. Start PostgreSQL
docker run -d \
  --name vexa-postgres \
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  postgres:latest

# 3. Start transcription service (see services/transcription-service/README.md)
cd services/transcription-service/
docker compose -f docker-compose.cpu.yml up -d

# 4. Start Vexa Lite
docker run -d \
  --name vexa \
  --network vexa-network \
  --add-host=host.docker.internal:host-gateway \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-transcription-api-key" \
  vexaai/vexa-lite:latest
```

---

## Environment Variables Reference

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string | `postgresql://user:pass@host:5432/vexa` |
| `ADMIN_API_TOKEN` | Yes | Secret token for admin operations | `your-secret-admin-token` |
| `TRANSCRIBER_URL` | Yes | Transcription service endpoint | `https://transcription.example.com/v1/audio/transcriptions` |
| `TRANSCRIBER_API_KEY` | Yes | API key for transcription service | `your-api-key` |
| `DB_SSL_MODE` | Optional | SSL mode for database connection | `require` (for Supabase) |

---

## Next Steps

- Get your API key: See [docs/self-hosted-management.md](self-hosted-management.md)
- Test the deployment: Follow `nbs/0_basic_test.ipynb`
- Full Docker Compose setup: See [docs/deployment.md](deployment.md)
