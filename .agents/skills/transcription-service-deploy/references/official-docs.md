# Official docs

Use only these sources for this skill:

- Deployment overview: https://github.com/Vexa-ai/vexa/blob/main/deploy/README.md
- Transcription service docs linked from deploy overview: https://github.com/Vexa-ai/vexa/blob/main/services/transcription-service/README.md

Raw sources:

- https://raw.githubusercontent.com/Vexa-ai/vexa/main/deploy/README.md
- https://raw.githubusercontent.com/Vexa-ai/vexa/main/services/transcription-service/README.md

## Required official checks

- Copy and configure `.env` from `.env.example`.
- Start GPU mode with `docker compose up -d`, or CPU mode with `docker compose -f docker-compose.cpu.yml up -d`.
- Watch `docker compose logs -f` until the model has loaded successfully.
- Check `curl http://localhost:8083/health`.
- Transcribe `tests/test_audio.wav` through `/v1/audio/transcriptions` with `X-API-Key: $API_TOKEN`.
- Run `bash tests/test_hot.sh --verify` when the repo includes it and the service is running.

## Hard limits

- Do not substitute unrelated Vexa Platform Kubernetes docs.
- Do not use docs.vexa.ai pages as deployment instructions for this skill.
- Do not invent smoke tests beyond the official curl/test-hot path.
