- **The model spend cap now holds inside a long turn.** The model proxy prices every call it relays
  from the provider's own usage (streamed or not, Anthropic and OpenAI dialects) at the ledger's price
  table, and checks the person's and the deployment's caps against the ledger plus that running figure
  before each model request. A long Claude Code turn is now stopped at the cap partway through (402)
  instead of spending until it ends. The worker's report at the end of the turn replaces the proxy's
  figure, so nothing is counted twice; a Codex turn, which reports no usage, is now counted at the
  proxy's figure. Turns on a deployment subscription are unchanged. See
  [Configuration](/configuration).
