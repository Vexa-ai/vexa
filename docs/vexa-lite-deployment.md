# Vexa Lite Deployment Guide

Deploy Vexa as a single Docker container with no GPU requirements. Vexa Lite is a stateless container that connects to external database and transcription services.

> 🚀 **One-click platform deployments**: For platform-specific deployment guides (Fly.io, Railway, Render, etc.), see the [vexa-lite-deploy repository](https://github.com/Vexa-ai/vexa-lite-deploy).

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

## Complete Setup Examples

### Example 1: Remote Database + Remote Transcription

**Best for:** Production deployments, fastest setup

**Pros:** `gpu-free` `serverless ready` `managed backups` `production ready` `scalable` 

**Cons:** `external services` `ongoing costs`

**Setup steps:**

1. **Create Supabase database:**
   - Create a new project at [supabase.com](https://supabase.com)
   - On the project page, click **Connect** button
   - Select method: **Session pooler**
   - Copy your connection string (example format):
     ```
     postgresql://postgres.your_project_id:[YOUR-PASSWORD]@aws-1-eu-west-1.pooler.supabase.com:5432/postgres
     ```
   - Replace `[YOUR-PASSWORD]` with your actual database password

2. **Get transcription API key:**

   - Get your API key from [https://staging.vexa.ai/dashboard/transcription](https://staging.vexa.ai/dashboard/transcription)
   - `TRANSCRIBER_URL` = `https://transcription.vexa.ai/v1/audio/transcriptions`

3. **Run Vexa Lite:**
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-api-key" \
  vexaai/vexa-lite:latest
```

---

### Example 2: Local Database + Remote Transcription

**Best for:** Development, quick testing

**Pros:** `gpu free` `fast start`  `dev friendly` `lower cost`

**Cons:** `no managed backups` `external transcription` `local db management`

**Setup steps:**

1. **Create network:**
```bash
docker network create vexa-network
```

2. **Start PostgreSQL:**
```bash
docker run -d \
  --name vexa-postgres \
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  postgres:latest
```

3. **Get transcription API key:**
   - Get your API key from [https://staging.vexa.ai/dashboard/transcription](https://staging.vexa.ai/dashboard/transcription)
   - `TRANSCRIBER_URL` = `https://transcription.vexa.ai/v1/audio/transcriptions`

4. **Run Vexa Lite:**
```bash
docker run -d \
  --name vexa \
  --network vexa-network \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-api-key" \
  vexaai/vexa-lite:latest
```

**Note:** Vexa container must use `--network vexa-network` to connect to local PostgreSQL.

---

### Example 3: Remote Database + Local Transcription

**Best for:** Maximum privacy with managed database

**Pros:** `managed backups` `high availability` `on premise transcription` `production db`

**Cons:** `gpu required` `complex setup` `transcription management`

**Setup steps:**

1. **Create Supabase database:**
   - Create a new project at [supabase.com](https://supabase.com)
   - On the project page, click **Connect** button
   - Select method: **Session pooler**
   - Copy your connection string (example format):
     ```
     postgresql://postgres.your_project_id:[YOUR-PASSWORD]@aws-1-eu-west-1.pooler.supabase.com:5432/postgres
     ```
   - Replace `[YOUR-PASSWORD]` with your actual database password

2. **Start transcription service:**
```bash
cd services/transcription-service/
docker compose -f docker-compose.cpu.yml up -d
```

   For detailed setup instructions, configuration options, and troubleshooting, see [`services/transcription-service/README.md`](../services/transcription-service/README.md).

3. **Run Vexa Lite:**
```bash
docker run -d \
  --name vexa \
  --add-host=host.docker.internal:host-gateway \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-transcription-api-key" \
  vexaai/vexa-lite:latest
```

**Note:** Use `--add-host=host.docker.internal:host-gateway` to access the transcription service running on the host.

---

### Example 4: Local Database + Local Transcription

**Best for:** Complete self-hosting, full data sovereignty

**Pros:** `data sovereignty` `no external deps` `full control`

**Cons:** `gpu required` `self maintenance` `complex setup` `higher infra`

**Setup steps:**

1. **Create network:**
```bash
docker network create vexa-network
```

2. **Start PostgreSQL:**
```bash
docker run -d \
  --name vexa-postgres \
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  postgres:latest
```

3. **Start transcription service:**
```bash
cd services/transcription-service/
docker compose -f docker-compose.cpu.yml up -d
```

   For detailed setup instructions, configuration options, and troubleshooting, see [`services/transcription-service/README.md`](../services/transcription-service/README.md).

4. **Run Vexa Lite:**
```bash
docker run -d \
  --name vexa \
  --network vexa-network \
  --add-host=host.docker.internal:host-gateway \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-transcription-api-key" \
  vexaai/vexa-lite:latest
```

**Note:** Vexa container must use `--network vexa-network` to connect to local PostgreSQL, and `--add-host=host.docker.internal:host-gateway` to access the transcription service.

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

- Test the deployment: Follow `nbs/0_basic_test.ipynb`

## Platform-Specific Deployments

For one-click deployment configurations on specific platforms (Fly.io, Railway, Render, Google Cloud Run, AWS, etc.), see the **[vexa-lite-deploy repository](https://github.com/Vexa-ai/vexa-lite-deploy)**. It provides:
- Platform-specific configuration files
- Step-by-step deployment guides
- Environment variable templates
- Troubleshooting tips for each platform

