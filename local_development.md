# Vexa Local Development Guide

This guide covers setting up Vexa for local development, particularly with CPU support for systems without NVIDIA GPUs.

## Environment Setup

### Environment Variables

Before starting, ensure your `.env` file includes at least:

```
ADMIN_API_TOKEN=SUPER_SECRET_ADMIN_TOKEN
LANGUAGE_DETECTION_SEGMENTS=10
VAD_FILTER_THRESHOLD=0.5
WHISPER_MODEL_SIZE=tiny
DEVICE_TYPE=cpu # or cuda
```

### Whisper model download & configuration

- Create a virtual environment and install dependencies for the whisper model:

```bash
python -m venv whisper-env
source whisper-env/bin/activate
pip install -r requirements.txt
```

- Download the whisper model:

```bash
python download_model.py
```

- Configure the size of the model to use in `.env` (e.g., `WHISPER_MODEL_SIZE=base`). `tiny`, `base`, `small`, `medium`, `large-v3` are available. `tiny` is recommended for CPU.
- Configure the compute device type to use in `.env` (e.g., `DEVICE_TYPE=cpu` or `DEVICE_TYPE=cuda`).

### Building Required Components

1. **Build the vexa-bot image**:

   The bot-manager requires this image to create meeting bots:

   ```bash
   cd services/vexa-bot/core
   docker build -t vexa-bot:latest .
   cd ../../..  # Return to root directory
   ```

## Running Vexa with CPU Support

The default Vexa configuration requires an NVIDIA GPU for the WhisperLive transcription service. For development on machines without compatible GPUs, follow these instructions.

### Starting Services with CPU Support

1. **Start core services in stages** (this prevents Docker network issues):

```bash
# First, clean up any existing containers
docker compose down --remove-orphans

# Start essential services
docker compose up -d redis postgres transcription-collector

# Start WhisperLive CPU version (with force-recreate to avoid network issues)
docker compose --profile cpu up -d --force-recreate whisperlive-cpu

# Start API services
docker compose up -d admin-api bot-manager api-gateway
```

### Troubleshooting Docker Network Issues

If you encounter errors like `Error response from daemon: network X not found`:

1. Stop all containers:

   ```bash
   docker compose down --remove-orphans
   ```

2. Start services in stages as shown above

3. If problems persist, try restarting Docker Desktop completely

Make sure you've built the vexa-bot image as described in the setup section.

## Using the API

### Creating Users and API Keys

To use the API, you need to create a user and generate an API token:

1. **Create a user**:

```bash
curl -X POST http://localhost:8057/admin/users \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: SUPER_SECRET_ADMIN_TOKEN" \
  -d '{
  "email": "your-email@example.com",
  "name": "Your Name",
  "image_url": "",
  "max_concurrent_bots": 1
}'
```

2. **Note the user ID** from the response (e.g., `"id": 1`)

3. **Generate an API token** for the user:

```bash
curl -X POST http://localhost:8057/admin/users/1/tokens \
  -H "accept: application/json" \
  -H "X-Admin-API-Key: SUPER_SECRET_ADMIN_TOKEN"
```

4. **Save the API token** from the response - you'll use this in the `X-API-Key` header for API requests

### Making API Calls

The API gateway is available at http://localhost:8056

Example API calls:

```bash
# To create a bot
curl -X POST http://localhost:8056/bots \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_TOKEN" \
  -d '{
    "native_meeting_id": "xxx-xxxx-xxx",
    "platform": "google_meet"
  }'

# To get transcripts
curl -H "X-API-Key: YOUR_API_TOKEN" \
  http://localhost:8056/transcripts/google_meet/xxx-xxxx-xxx
```

## Additional Information

### Differences Between CPU and GPU Modes

When running in CPU mode:

- Transcription will be significantly slower than with GPU acceleration
- The system uses a smaller model size for better CPU performance
- WhisperLive runs on port 9092 instead of 9090
- The hostname is `whisperlive-cpu` instead of `whisperlive`

The bot manager automatically connects to the CPU version when using the CPU profile.
