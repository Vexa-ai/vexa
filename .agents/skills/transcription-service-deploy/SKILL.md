---
name: transcription-service-deploy
description: Deploy and verify the self-hosted Vexa transcription service using only official Vexa deploy-linked docs. Use when Codex is asked to run, troubleshoot, or validate services/transcription-service from the Vexa upstream repo, including GPU or CPU compose startup, model-loaded checks, health checks, and audio smoke tests.
---

# Transcription Service Deploy

## Start Here

Read `references/official-docs.md` first. Then read the upstream official sources it names before running or changing anything:

1. `deploy/README.md`
2. `services/transcription-service/README.md`

Use `services/transcription-service/README.md` only because `deploy/README.md` explicitly links it as the self-host transcription service source.

## Rules

- Follow only the official commands from those README files.
- Do not use docs-site deployment pages, local platform runbooks, archived notes, or invented wrapper commands.
- Do not add helper scripts or alternative deployment recipes.
- Do not print `API_TOKEN`, transcription tokens, database credentials, or any secret values.
- If the official docs are missing, contradictory, or do not cover the requested environment, stop and report the exact documentation gap.

## Deploy Flow

1. Confirm the target checkout is the upstream `Vexa-ai/vexa` repo or clone it using the official deploy docs.
2. Enter `services/transcription-service`.
3. Copy `.env.example` to `.env` if needed, then ensure required values are set without exposing secrets.
4. Select the official mode before starting:
   - Check local GPU availability first, for example `nvidia-smi -L`, and check Docker/GPU runtime usability when needed.
   - If a usable GPU is available, propose GPU mode to the user and ask for confirmation before starting.
   - If no usable GPU is available, propose CPU mode to the user and ask for confirmation before starting.
   - If the user already explicitly requested CPU or GPU mode, still report the detected GPU state and proceed with the requested official mode unless it is clearly impossible.
   - Before starting GPU mode, check for container-name or port collisions with existing Docker containers.
   - If GPU mode is otherwise usable but would collide with existing `transcription-lb` or `transcription-worker-*` containers, do not switch to CPU automatically. Ask the user whether you may change the GPU worker/load-balancer container names and any conflicting host ports for this local deployment.
   - Treat renaming GPU containers or changing conflicting host ports as a user-approved local workaround. Report exactly what was changed and why.
5. Start with the confirmed official mode:
   - GPU: `docker compose up -d`
   - CPU: `docker compose -f docker-compose.cpu.yml up -d`
6. Watch official logs until the model has loaded successfully.

## Completion Criteria

Treat the deploy as incomplete until all required official verification succeeds:

- Logs show the model loaded successfully.
- `curl http://localhost:8083/health` returns healthy.
- The official audio transcription smoke path succeeds against `tests/test_audio.wav`.
- Prefer `bash tests/test_hot.sh --verify` when available and appropriate.

Report the mode used, endpoint, verification commands, and final pass/fail state. Redact secrets.
