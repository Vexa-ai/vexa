- **No worker holds a model-provider API key or the toolbelt bearer (H5/W1).** Agent model calls now
  go through a new **model proxy** (the agent-api image as `model-proxy`, port 8102, on compose, helm
  and Lite). agent-api seals each turn's route — endpoint, key, models, the delegation token — for
  the proxy, and the worker gets only a per-turn token, revoked when the turn ends and refused once
  expired, for another turn, or for a model outside the turn's scope. Streaming is unchanged.
  Operators: model keys and model settings (`VEXA_RUNNER`, `VEXA_LLM_*`, `ANTHROPIC_*`, `OPENAI_API_KEY`,
  `CODEX_API_KEY`, `VEXA_AGENT_MODEL`) are read from **agent-api** only; the runtime no longer
  forwards them. `VEXA_MODEL_PROXY_SECRET` is generated at install (compose: `make` mints it into
  `.env`; with `secrets.existingSecretName`, add it to your Secret). A subscription
  (`CLAUDE_CODE_OAUTH_TOKEN`, `HOST_CLAUDE_CREDENTIALS`, `HOST_CODEX_CREDENTIALS`) still stays with the
  vendor's own CLI. See [Execution](/architecture/execution#the-model-proxy).
