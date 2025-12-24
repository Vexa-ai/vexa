# Run Local Transcription Service

```bash
cd /path/to/vexa-transcription-service

# Create .env file
cp .env.example .env
```

Edit `.env`:
```bash
MODEL_SIZE=large-v3-turbo
DEVICE=cuda
COMPUTE_TYPE=int8
API_TOKEN=cczM1VUk7FXaw6EwMrwMVdTwhqIiYAdmFVvUG1uF
```

Start service:
```bash
docker-compose up -d
```

Service runs on `http://localhost:8083/v1/audio/transcriptions`

