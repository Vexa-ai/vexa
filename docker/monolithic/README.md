# Vexa Monolithic Deployment

All-in-one Docker deployment for platforms without Docker socket access (EasyPanel, Dokploy, Railway, Render, etc.).

**Key features:**
- Single container with all services
- **Embedded Redis** (no external Redis required by default)
- Only needs PostgreSQL as external dependency
- Optional GPU support for faster transcription

## Quick Start

### CPU Version (Default - Simplest Setup)

```bash
# Build the image
docker build -f Dockerfile.monolithic -t vexa-monolithic .

# Run with just PostgreSQL (Redis is embedded!)
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -p 8057:8057 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  vexa-monolithic
```

### With External Redis (Optional)

If you prefer to use an external Redis server (for high-availability, persistence, etc.):

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -p 8057:8057 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e REDIS_URL="redis://:password@host:6379/0" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  vexa-monolithic
```

### GPU Version (NVIDIA)

```bash
# Build with GPU support (CUDA 12.4 - default, recommended)
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu -t vexa-monolithic:gpu .

# Build with specific CUDA version (for older/newer drivers)
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=11.8 -t vexa-monolithic:gpu-compat .
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=12.6 -t vexa-monolithic:gpu-latest .

# Build for RTX 5000 series (Blackwell) - RTX 5070/5080/5090
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=12.8 -t vexa-monolithic:gpu-blackwell .

# Run with GPU acceleration (Redis embedded by default)
docker run -d \
  --name vexa \
  --gpus all \
  -p 8056:8056 \
  -p 8057:8057 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e WHISPER_MODEL_SIZE=medium \
  vexa-monolithic:gpu
```

**Endpoints:**
- API Gateway: `http://localhost:8056/docs`
- Admin API: `http://localhost:8057/docs`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Monolithic Container                         │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐            │
│  │ API Gateway │  │  Admin API  │  │ Bot Manager  │            │
│  │   :8056     │  │    :8057    │  │    :8080     │            │
│  └─────────────┘  └─────────────┘  └──────┬───────┘            │
│                                           │                     │
│                                    spawns processes             │
│                                           ↓                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Bot Processes (Node.js/Playwright)          │   │
│  │         bot-1 (pid)    bot-2 (pid)    bot-3 (pid)       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          │                                      │
│                     audio stream                                │
│                          ↓                                      │
│  ┌─────────────────┐           ┌─────────────────────────┐     │
│  │   WhisperLive   │──Redis───▶│ Transcription Collector │     │
│  │     :9090       │  Stream   │         :8123           │     │
│  │   (CPU/GPU)     │           └─────────────────────────┘     │
│  └─────────────────┘                                           │
│                          │                                      │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │           Redis (embedded, :6379)                        │   │
│  │    Streams, Pub/Sub, Key-Value for service communication │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Xvfb (:99)                            │   │
│  │              Virtual Display for Browsers                │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                       ┌──────────┐
                       │ Postgres │
                       │(external)│
                       └──────────┘

Alternative: Use REDIS_URL to connect to external Redis instead of embedded.
```

**Key difference from standard deployment:** Instead of spawning Docker containers for bots, the monolithic version uses a **process orchestrator** that spawns bots as Node.js child processes within the same container.

## GPU Support

### Requirements

For GPU-accelerated transcription, you need:

1. **NVIDIA GPU** (GTX 1080+, RTX series, Tesla, etc.)
2. **NVIDIA Drivers** (version depends on CUDA version - see table below)
3. **NVIDIA Container Toolkit**

| CUDA Version | Minimum Driver | Supported GPUs |
|--------------|----------------|----------------|
| 11.8 | 520+ | GTX 1080+, RTX 2000/3000/4000 series |
| 12.1 | 530+ | GTX 1080+, RTX 2000/3000/4000 series |
| 12.4 | 550+ | RTX 2000/3000/4000 series (recommended) |
| 12.6 | 560+ | RTX 3000/4000 series, latest features |
| 12.8 | 570+ | **RTX 5000 series (Blackwell) - RECOMMENDED** |
| 12.9 | 570+ | Blackwell architecture (experimental) |

```bash
# Verify GPU is available and check driver version
nvidia-smi

# Test Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### Installing NVIDIA Container Toolkit

```bash
# Ubuntu/Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### GPU Build Details

#### CUDA Version Selection

Choose the CUDA version based on your NVIDIA driver version:

| CUDA_VERSION | Driver Required | PyTorch Index | Best For |
|--------------|-----------------|---------------|----------|
| `11.8` | 520+ | cu118 | Maximum compatibility, older servers |
| `12.1` | 530+ | cu121 | Good compatibility |
| `12.4` (default) | 550+ | cu124 | **Recommended** - RTX 2000/3000/4000 |
| `12.6` | 560+ | cu126 | Recent RTX 4000 hardware |
| `12.8` | 570+ | cu128 | **RTX 5000 series (Blackwell)** |
| `12.9` | 570+ | cu126 | Experimental Blackwell |

**Check your driver version:**
```bash
nvidia-smi  # Look for "Driver Version" and "CUDA Version" at the top
```

#### Build Examples

```bash
# Default (CUDA 12.4) - works with most modern servers
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu -t vexa:gpu .

# Maximum compatibility (CUDA 11.8) - works with older drivers
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=11.8 -t vexa:gpu-compat .

# RTX 5000 series / Blackwell (CUDA 12.8) - RECOMMENDED for RTX 5070/5080/5090
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=12.8 -t vexa:gpu-blackwell .

# Experimental Blackwell (CUDA 12.9)
docker build -f Dockerfile.monolithic --build-arg DEVICE=gpu --build-arg CUDA_VERSION=12.9 -t vexa:gpu-latest .
```

### Startup Output (GPU)

When running the GPU version, you'll see detailed hardware information:

```
==============================================
  Vexa Monolithic - Starting Container
==============================================

Hardware Detection:
-------------------
  Build Type: GPU (CUDA 12.4)
  NVIDIA Driver: Available

  Detected GPU(s):
    [0] NVIDIA GeForce RTX 4070
        Memory: 12227 MB
        Driver: 550.120
        Compute Capability: 8.9

  Total GPUs: 1

  CUDA Validation:
    PyTorch CUDA: Available
    CUDA Version: 12.4
    cuDNN Version: 90100
    Device 0: NVIDIA GeForce RTX 4070 (SM 8.9)

  Auto-configured DEVICE_TYPE=cuda
```

### Selecting GPU

```bash
# Use specific GPU
docker run --gpus '"device=0"' ...

# Use multiple GPUs
docker run --gpus '"device=0,1"' ...

# Use all GPUs
docker run --gpus all ...
```

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql://user:pass@host:5432/vexa` |
| `ADMIN_API_TOKEN` | Secret token for admin operations | `your-secret-token-here` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | embedded | External Redis URL (if not set, uses embedded Redis) |
| `WHISPER_MODEL_SIZE` | `tiny` | Whisper model size (see below) |
| `LOG_LEVEL` | `info` | Logging level (debug, info, warning, error) |
| `DEVICE_TYPE` | auto | Device type (`cpu` or `cuda`, auto-detected for GPU builds) |

### Redis Configuration

**By default, Redis is embedded** in the container and requires no configuration.

To use an external Redis server (for high-availability, persistence, or shared state):

```bash
# Via URL
-e REDIS_URL="redis://:password@host:6379/0"

# Or via individual variables
-e REDIS_HOST=redis.example.com
-e REDIS_PORT=6379
-e REDIS_PASSWORD=your-redis-password
```

### Alternative Configuration (Individual Variables)

Instead of URLs, you can use individual variables:

```bash
# Database
DB_HOST=postgres.example.com
DB_PORT=5432
DB_NAME=vexa
DB_USER=postgres
DB_PASSWORD=your-password
```

## Whisper Model Selection

### CPU Recommendations

| Model | Size | Quality | Speed | Recommended For |
|-------|------|---------|-------|-----------------|
| `tiny` | ~75MB | Basic | Fast | Development, testing |
| `small` | ~500MB | Good | Medium | Light production |

### GPU Recommendations

| Model | VRAM | Quality | Speed | Recommended For |
|-------|------|---------|-------|-----------------|
| `tiny` | ~1GB | Basic | Very Fast | Testing |
| `small` | ~2GB | Good | Fast | Light usage |
| `medium` | ~5GB | Better | Medium | **Production (recommended)** |
| `large-v3` | ~10GB | Best | Slower | High-quality requirements |

```bash
# GPU with medium model (recommended)
docker run -d --gpus all \
  -e WHISPER_MODEL_SIZE=medium \
  -e DATABASE_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  vexa-monolithic:gpu
```

**Note:** Models are downloaded on first use. Mount a volume to persist them.

## Persistent Storage (Volumes)

For production deployments, mount volumes to persist data:

```bash
docker run -d \
  --name vexa \
  --gpus all \
  -p 8056:8056 \
  -p 8057:8057 \
  -v vexa-models:/root/.cache/huggingface \
  -v vexa-logs:/var/log/vexa-bots \
  -e DATABASE_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  vexa-monolithic:gpu
```

| Volume | Path | Description |
|--------|------|-------------|
| `vexa-models` | `/root/.cache/huggingface` | Downloaded Whisper models (avoid re-downloading) |
| `vexa-logs` | `/var/log/vexa-bots` | Bot process logs |

## Platform-Specific Deployment

### EasyPanel

1. Create a new **App** from Git repository or Docker image
2. Configure environment variables:
   - `DATABASE_URL` → Use EasyPanel PostgreSQL service URL
   - `ADMIN_API_TOKEN` → Generate a secure token
   - `REDIS_URL` → (Optional) Use EasyPanel Redis service URL, or leave empty for embedded Redis
3. Expose ports: `8056` (API), `8057` (Admin)
4. Optional: Add persistent volumes for models and logs

### Dokploy

1. Create a new **Application** → Docker deployment
2. Use `Dockerfile.monolithic` or pre-built image
3. Set environment variables in Dokploy's env section
4. Only PostgreSQL is required (Redis is embedded by default)

### Railway / Render

1. Deploy from GitHub with `Dockerfile.monolithic`
2. Add PostgreSQL as managed service (Redis is embedded)
3. Configure `DATABASE_URL` and `ADMIN_API_TOKEN`
4. Set exposed port to `8056`

## Management

### View Logs

```bash
# All services (stdout)
docker logs vexa

# Follow logs
docker logs -f vexa

# Specific service logs (inside container)
docker exec vexa cat /var/log/supervisor/api-gateway.log
docker exec vexa cat /var/log/supervisor/bot-manager.log
docker exec vexa cat /var/log/supervisor/whisperlive.log
```

### Service Status

```bash
docker exec vexa supervisorctl status
```

Output:
```
vexa-core:redis                  RUNNING   pid 122, uptime 0:05:00  (embedded, or STOPPED if using external)
vexa-core:admin-api              RUNNING   pid 123, uptime 0:05:00
vexa-core:api-gateway            RUNNING   pid 124, uptime 0:05:00
vexa-core:bot-manager            RUNNING   pid 125, uptime 0:05:00
vexa-core:transcription-collector RUNNING   pid 126, uptime 0:05:00
vexa-core:whisperlive            RUNNING   pid 127, uptime 0:05:00
vexa-core:xvfb                   RUNNING   pid 128, uptime 0:05:00
```

### Restart a Service

```bash
docker exec vexa supervisorctl restart vexa-core:whisperlive
docker exec vexa supervisorctl restart vexa-core:bot-manager
```

## Testing

### Create a User and Get API Key

```bash
# Create user (via Admin API)
curl -X POST "http://localhost:8057/users" \
  -H "X-Admin-Token: your-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "name": "Test User"}'

# Response includes API key:
# {"id": 1, "email": "test@example.com", "api_key": "vx_abc123..."}
```

### Start a Bot

```bash
curl -X POST "http://localhost:8056/bots" \
  -H "X-API-Key: vx_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "Vexa Bot",
    "language": "en"
  }'
```

### Get Transcription

```bash
curl "http://localhost:8056/transcripts/google_meet/abc-defg-hij" \
  -H "X-API-Key: vx_abc123..."
```

## Comparison: CPU vs GPU vs Standard

| Feature | Standard (Docker Compose) | Monolithic CPU | Monolithic GPU |
|---------|---------------------------|----------------|----------------|
| **Services** | Multiple containers | Single container | Single container |
| **Bot Spawning** | Docker containers | Node.js processes | Node.js processes |
| **Docker Socket** | Required | Not required | Not required |
| **Transcription Speed** | GPU (fast) | CPU (slow) | GPU (fast) |
| **Recommended Model** | medium/large | tiny/small | medium/large |
| **Max Concurrent Bots** | Unlimited* | 3-5 | 5-10+ |
| **Image Size** | ~2GB each | ~4GB | ~8GB |
| **Use Case** | Production | Simple/PaaS | PaaS with GPU |

## Limitations

### CPU Version
- **Slower Transcription:** CPU inference is 5-10x slower than GPU
- **Smaller Models:** Large models may be too slow
- **Concurrent Bots:** Max 3-5 recommended

### GPU Version
- **NVIDIA Only:** Requires NVIDIA GPU and drivers
- **Container Toolkit:** Requires nvidia-container-toolkit
- **Platform Support:** Not all PaaS platforms support GPU

## Troubleshooting

### Bot Fails to Start

```bash
# Check bot manager logs
docker logs vexa 2>&1 | grep -i "bot-manager"

# Verify Xvfb is running (required for browsers)
docker exec vexa supervisorctl status vexa-core:xvfb
```

### GPU Not Detected

```bash
# Check if --gpus flag was passed
docker exec vexa nvidia-smi

# Verify CUDA in Python
docker exec vexa python3 -c "import torch; print(torch.cuda.is_available())"
```

### Transcriptions Not Appearing

```bash
# Check WhisperLive Redis connection
docker logs vexa 2>&1 | grep -i "redis"

# Verify Redis stream URL is set correctly
docker exec vexa env | grep REDIS
```

### High Memory Usage

- Use a smaller Whisper model (`tiny` or `small` for CPU)
- Limit concurrent bots
- Increase container memory limits

## Files

| File | Description |
|------|-------------|
| `Dockerfile.monolithic` | Main Dockerfile with CPU/GPU support |
| `docker/monolithic/supervisord.conf` | Supervisor configuration |
| `docker/monolithic/entrypoint.sh` | Container initialization with GPU detection |
| `docker/monolithic/requirements-monolithic.txt` | Python dependencies |
| `services/bot-manager/app/orchestrators/process.py` | Process orchestrator |

## Changes from Open Source Project

The monolithic deployment adds the following without modifying core service code:

**New Files:**
- `Dockerfile.monolithic` - All-in-one container build (CPU/GPU) with embedded Redis
- `docker/monolithic/*` - Configuration files
- `services/bot-manager/app/orchestrators/process.py` - Process-based bot spawner

**Minimal Modifications:**
- `services/bot-manager/app/orchestrators/__init__.py` - Loads process orchestrator when `ORCHESTRATOR=process`
- `services/transcription-collector/config.py` - Added `REDIS_PASSWORD` support
- `services/transcription-collector/main.py` - Password parameter in Redis connection

**Key Features:**
- **Embedded Redis** - No external Redis required by default (256MB memory limit)
- **Optional external Redis** - Set `REDIS_URL` to use your own Redis server
- **Auto-detection** - Container automatically uses embedded or external Redis

All changes are **backwards compatible** and don't affect standard Docker Compose deployment.
