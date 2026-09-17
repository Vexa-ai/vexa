- **Custom model endpoints: the two call shapes are configurable separately, and a wrong answer
  now says what is wrong (#1666).** `mode: custom` drives two different wire contracts — agent
  turns post `{base}/v1/messages` with `x-api-key`, meeting summaries post
  `{base}/chat/completions` with a bearer token — and one `base_url` plus one `api_key` could not
  serve a gateway that splits them. Settings → Models takes `harness_base_url` and
  `harness_api_key` for the Messages side (each falling back to its `base_url`/`api_key` twin, so
  a gateway serving both dialects is configured exactly as before). A gateway that answers HTTP
  200 with the other dialect's body, or with a CDN's HTML page, now fails by name instead of
  yielding an empty completion, and a rejected credential is terminal rather than retried. The
  Settings → Models **Test** button probes each call shape at its own path with its own auth
  header and grades the body it got back — it used to send both auth headers at once and grade the
  status code, so it passed configurations under which no turn could run.

- **Custom model endpoints accept provider-required extra headers (#1667).** A `headers` field on
  the model config (`Name: Value` lines, or a JSON object) rides both call shapes — the completion
  adapters read `VEXA_LLM_EXTRA_HEADERS`, the agent harness reads `ANTHROPIC_CUSTOM_HEADERS` — so
  a gateway that wants a session, routing, organisation or entitlement header of its own no longer
  needs a translating proxy in front of it. Values are masked on read-back like `api_key`, header
  names are not, and the extras never override the auth header.
