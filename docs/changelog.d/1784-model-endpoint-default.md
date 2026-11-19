- **The model endpoint allow-list ships hosted gateways only (#1784).** Unset,
  `VEXA_MODEL_BASE_URL_ALLOW` admits the deployment's own gateway plus `api.anthropic.com` and
  `openrouter.ai`. A private inference server must be named in it explicitly.
