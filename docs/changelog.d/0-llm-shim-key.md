- **The llm-shim takes a key and moves to LiteLLM v1.108.0.** Set `LLM_SHIM_KEY` in
  `deploy/compose/.env` and use the same value as the `api_key` of the model config that points at
  the shim; without it the shim does not start. The move closes 17 HIGH advisories in the old
  image. See [Upgrade from 0.12](/upgrade-from-0-12).
