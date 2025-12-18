# Vexa-light Container


Now you can run Vexa as a single docker container with no GPU requirements, because we 

- Moved transcription work outside
- Built a single containerised service with everything inside


Why we wrapped Vexa into a single container:

- Easy to deploy locally or in any serverless provider
- The container is stateless - has no permanent data; all data is stored in the database, which is connected from outside
- No GPU requirements, because transcription is outside the container

You will need to connect your container to the database and transcription server.

Transcription service: you can self host the service or just grab an API key from service run by Vexa Team (which is just running the same transcription server behind the hood on our GPUs)

The full Docker Compose Vexa deployment is available as well, though significantly updated with transcription moved to a separate service.


YOu can run with 

- local transcription
- remote transcription

- local database
- remote database


1. remote trnascription / local database

docker network create vexa-network

docker run -d \
  --name vexa-postgres\
  --network vexa-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=testpass \
  -e POSTGRES_DB=vexa \
  -p 5432:5432 \
  -v postgres-test-data:/var/lib/postgresql \
  --restart unless-stopped \
  postgres:latest


docker run -d \
  --name vexa \
  --network vexa-network \
  -p 8060:8056 \
  -e DATABASE_URL="postgresql://postgres:testpass@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="test-token" \
  -e TRANSCRIBER_URL="https://transcription-gateway.dev.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="cczM1VUk7FXaw6EwMrwMVdTwhqIiYAdmFVvUG1uF" \
  vexa-lite:latest



  2. remote trnanscription / remote database


  docker run -d --name vexa-supabase -p 8060:8056 -e DATABASE_URL="postgresql://postgres.fghfjzpqncuawqurtxwb:FRkb3ff6SQPsw4rE@aws-1-eu-west-1.pooler.supabase.com:5432/postgres" -e DB_SSL_MODE="require" -e ADMIN_API_TOKEN="test-token" -e TRANSCRIBER_URL="https://transcription-gateway.dev.vexa.ai/v1/audio/transcriptions" -e TRANSCRIBER_API_KEY="cczM1VUk7FXaw6EwMrwMVdTwhqIiYAdmFVvUG1uF" vexa-lite:latest





  3. local transcription / local database

  docker network create vexa-network

cd vexa/services/transcription-service/
(base) dima@bbb:~/dev/vexa/services/transcription-service$ docker compose up -d


docker run -d \
  --name vexa \
  --network vexa-network \
  --add-host=host.docker.internal:host-gateway \
  -p 8060:8056 \
  -e DATABASE_URL="postgresql://postgres:testpass@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="test-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="transcription_service_secret_token_12345" \
  vexa-lite:latest








## 1. Get Transcription API Key

### Option A: Use Hosted Service

Get API key from your hosted transcription service.


- `TRANSCRIBER_URL` = `https://transcription.vexa.ai/v1/audio/transcriptions`)
- `TRANSCRIBER_API_KEY`: Your API key

### Option B: Run Local Transcription Service

See [LOCAL_TRANSCRIPTION_SETUP.md](./LOCAL_TRANSCRIPTION_SETUP.md)

---

## 2. Choose Database


### Option A: Use hosted database 

#### Supabase

1. Create project at [supabase.com](https://supabase.com)
2. Get connection details from **Settings** → **Database**
3. Use connection pooler URL: `postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres`

`DATABASE_URL` = `postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres`


### Option B: Run Local PostgreSQL

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

`DATABASE_URL` = `postgresql://postgres:your_password@vexa-postgres:5432/vexa`



---

## 3. Run Vexa Docker Container

### Simple Example

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-api-key" \
  vexaai/vexa-lite:latest
```

### Run Container

**Local PostgreSQL + Local Transcription:**
```bash
docker run -d \
  --name vexa \
  --network vexa-network \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="cczM1VUk7FXaw6EwMrwMVdTwhqIiYAdmFVvUG1uF" \
  vexaai/vexa-lite:latest
```
*Note: `--network vexa-network` is required for local PostgreSQL to connect via container name (`vexa-postgres`)*

**Local PostgreSQL + Hosted Transcription:**
```bash
docker run -d \
  --name vexa \
  --network vexa-network \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres:your_password@vexa-postgres:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-hosted-api-key" \
  vexaai/vexa-lite:latest
```
*Note: `--network vexa-network` is required for local PostgreSQL to connect via container name (`vexa-postgres`)*

**Supabase + Local Transcription:**
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="http://host.docker.internal:8083/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="cczM1VUk7FXaw6EwMrwMVdTwhqIiYAdmFVvUG1uF" \
  vexaai/vexa-lite:latest
```

**Supabase + Hosted Transcription:**
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://postgres.your_project_id:password@aws-0-us-west-2.pooler.supabase.com:5432/postgres" \
  -e DB_SSL_MODE="require" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="your-hosted-api-key" \
  vexaai/vexa-lite:latest
```

API available at: `http://localhost:8056`
