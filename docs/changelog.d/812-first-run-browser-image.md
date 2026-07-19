- **Cold first-run no longer dies on `invalid reference format` (#812).** Stock `.env.example` ships
  `BROWSER_IMAGE=vexaai/vexa-bot:${IMAGE_TAG}` — docker-compose expands it, but `make all`'s
  spawn-image pull read the literal string and handed `docker pull` an invalid reference, breaking
  every fresh install at the last step. The pull now resolves `${IMAGE_TAG}` (and hardens the tag
  read against duplicate lines); a genuinely custom literal `BROWSER_IMAGE` still pulls verbatim.
