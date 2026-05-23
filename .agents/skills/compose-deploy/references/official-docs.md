# Official docs

Use only these sources for this skill:

- Deployment overview: https://github.com/Vexa-ai/vexa/blob/main/deploy/README.md
- Docker Compose deploy README: https://github.com/Vexa-ai/vexa/blob/main/deploy/compose/README.md

Raw sources:

- https://raw.githubusercontent.com/Vexa-ai/vexa/main/deploy/README.md
- https://raw.githubusercontent.com/Vexa-ai/vexa/main/deploy/compose/README.md

## Required official checks

- Normal deploy: `cd deploy/compose && make all`.
- Source-build deploy only when explicitly requested: `cd deploy/compose && make all-build`.
- Verify:
  - `make test`
  - `make test-transcription` when transcription reachability is in scope
  - `make ps`
- Stop/cleanup when requested:
  - `make down && docker compose ps`

## Hard limits

- Do not use Lite or Helm commands for compose deployment.
- Do not use local Vexa Platform runbooks as compose deployment instructions.
- Do not invent checks beyond official Make targets unless the user explicitly asks for extra investigation.
